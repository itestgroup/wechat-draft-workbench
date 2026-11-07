import base64
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("deploy_scf", ROOT / "scripts/deploy_scf.py")
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


class Request:
    pass


MODELS = SimpleNamespace(GetFunctionRequest=Request, UpdateFunctionCodeRequest=Request, GetFunctionAddressRequest=Request, GetAliasRequest=Request)


class Client:
    def __init__(self, package, states=None, handler="scf_probe.main_handler", hashes=None):
        self.package = package
        self.states = iter(states or ["Active", "Active"])
        self.handler = handler
        self.hashes = iter(hashes or [hashlib.sha256(package).hexdigest()])
        self.requests = []

    def GetFunction(self, request):
        assert request.Qualifier == "$LATEST"
        assert request.ShowCode == "FALSE"
        return SimpleNamespace(Status=next(self.states), Runtime="Python3.9", Handler=self.handler)

    def UpdateFunctionCode(self, request):
        self.requests.append(request)
        return SimpleNamespace(RequestId="test-request")

    def GetFunctionAddress(self, request):
        return SimpleNamespace(CodeSha256=next(self.hashes), Url="signed-url-must-not-be-logged")

    def GetAlias(self, request):
        assert request.Name == "$DEFAULT"
        return self.alias


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(deployment, "FUNCTION", "labview-jobs")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_package_has_root_handler_and_excludes_credentials(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            (root / "scf_probe.py").write_text("def main_handler(event, context):\n    return {}\n")
            (root / "wechat_client.py").write_text("x=1")
            (root / "wechat_draft_probe.py").write_text("x=1")
            for name in deployment.PACKAGE_FILES:
                if not (root / name).exists():
                    (root / name).write_text('x=1')
            (root / ".env").write_text("SECRET=must-not-upload")
            (root / "credentials.json").write_text('{"key":"must-not-upload"}')
            (root / "__pycache__").mkdir()
            (root / "__pycache__/cache.py").write_text("x=1")
            package = deployment.build_package(root)
            self.assertEqual(package, deployment.build_package(root))
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                self.assertEqual(archive.namelist(), sorted(deployment.PACKAGE_FILES))
            self.assertNotIn(b"must-not-upload", package)

    def test_missing_handler_and_invalid_syntax_fail_before_upload(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            with self.assertRaises(ValueError):
                deployment.build_package(folder)
            for name in deployment.PACKAGE_FILES:
                (Path(folder) / name).write_text("def broken(")
            with self.assertRaises(SyntaxError):
                deployment.build_package(folder)

    def test_runtime_sdk_is_packaged_and_hidden_files_excluded(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            runtime = Path(folder)
            (runtime / 'qcloud_cos').mkdir()
            (runtime / 'qcloud_cos/__init__.py').write_text('')
            (runtime / '.env').write_text('DO_NOT_UPLOAD')
            (runtime / '__pycache__').mkdir()
            (runtime / '__pycache__/cached.pyc').write_bytes(b'cache')
            package = deployment.build_package(ROOT, runtime)
            self.assertEqual(package, deployment.build_package(ROOT, runtime))
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                self.assertIn('qcloud_cos/__init__.py', archive.namelist())
                self.assertNotIn('.env', archive.namelist())
                self.assertNotIn('__pycache__/cached.pyc', archive.namelist())

    @unittest.skipIf(__import__("os").name == "nt", "Windows symlink privilege varies")
    def test_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            root = Path(folder)
            (root / "original.py").write_text("x=1")
            (root / "scf_probe.py").symlink_to(root / "original.py")
            (root / "wechat_client.py").write_text("x=1")
            (root / "wechat_draft_probe.py").write_text("x=1")
            for name in deployment.PACKAGE_FILES:
                if not (root / name).exists():
                    (root / name).write_text('x=1')
            with self.assertRaises(ValueError):
                deployment.build_package(root)

    def test_updates_code_only_and_waits_for_matching_hash(self):
        package = deployment.build_package(ROOT)
        digest = hashlib.sha256(package).hexdigest()
        client = Client(package, ["Active", "Updating", "Active", "Active"], hashes=["old-hash", digest])
        deployment.deploy(client, MODELS, package, sleep=lambda _: None)
        self.assertEqual(len(client.requests), 1)
        request = client.requests[0]
        self.assertEqual(set(vars(request)), {"FunctionName", "Namespace", "CodeSource", "ZipFile", "Publish"})
        self.assertEqual(request.FunctionName, "labview-jobs")
        self.assertEqual(request.Namespace, "default")
        self.assertEqual(request.Publish, "FALSE")
        self.assertEqual(base64.b64decode(request.ZipFile), package)

    def test_preflight_failure_does_not_mutate(self):
        for states, handler in [(["Updating"], "scf_probe.main_handler"), (["Active"], "other.handler")]:
            client = Client(b"package", states, handler)
            with self.assertRaises(RuntimeError):
                deployment.deploy(client, MODELS, b"package", sleep=lambda _: None)
            self.assertEqual(client.requests, [])

    def test_failed_state_and_hash_timeout_are_errors(self):
        for states, hashes in [(["Active", "UpdateFailed"], None), (["Active", "Active"], ["wrong-hash"])]:
            client = Client(b"package", states, hashes=hashes)
            with self.assertRaises(RuntimeError):
                deployment.deploy(client, MODELS, b"package", sleep=lambda _: None, attempts=1)

    def test_refuses_unconfirmed_url_target(self):
        with patch.dict("os.environ", {"SCF_URL_QUALIFIER": "release-alias"}):
            with self.assertRaises(ValueError):
                deployment.main()

    def test_missing_target_and_existing_project_are_rejected(self):
        for name in ('', 'Wecom-push'):
            with patch.object(deployment, 'FUNCTION', name), patch.object(deployment, 'create_client') as create:
                with self.assertRaises(ValueError):
                    deployment.main()
                create.assert_not_called()

    def test_default_alias_to_latest_is_checked_before_and_after_update(self):
        client = Client(b"package")
        client.alias = SimpleNamespace(FunctionVersion="$LATEST", RoutingConfig=None)
        with patch.object(client, "GetAlias", wraps=client.GetAlias) as read:
            deployment.deploy(client, MODELS, b"package", sleep=lambda _: None, qualifier="$DEFAULT")
            self.assertEqual(read.call_count, 2)
        self.assertEqual(len(client.requests), 1)

    def test_default_split_traffic_or_other_primary_never_updates_code(self):
        aliases = [
            SimpleNamespace(FunctionVersion="1", RoutingConfig=None),
            SimpleNamespace(FunctionVersion="$LATEST", RoutingConfig=SimpleNamespace(
                AdditionalVersionWeights=[SimpleNamespace(Version="1", Weight=0.1)], AddtionVersionMatchs=[])),
            SimpleNamespace(FunctionVersion="$LATEST", RoutingConfig=SimpleNamespace(
                AdditionalVersionWeights=[], AddtionVersionMatchs=[SimpleNamespace(Version="2")])),
            SimpleNamespace(FunctionVersion="$LATEST", RoutingConfig=SimpleNamespace(
                AdditionalVersionWeights=[SimpleNamespace(Version="1", Weight=float('nan'))], AddtionVersionMatchs=[])),
        ]
        for alias in aliases:
            client = Client(b"package")
            client.alias = alias
            with self.assertRaises(RuntimeError):
                deployment.deploy(client, MODELS, b"package", sleep=lambda _: None, qualifier="$DEFAULT")
            self.assertEqual(client.requests, [])

    def test_default_alias_permission_error_never_updates_code(self):
        client = Client(b"package")
        with patch.object(client, "GetAlias", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                deployment.deploy(client, MODELS, b"package", sleep=lambda _: None, qualifier="$DEFAULT")
        self.assertEqual(client.requests, [])


if __name__ == "__main__":
    unittest.main()

