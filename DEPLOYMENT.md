# ����·��������

�������������嵥λ��scripts/requirements-runtime.lock��Ŀ��ΪLinux x86_64 / Python3.10������ʱʹ�� `pip install --require-hashes --target .build/scf-runtime -r scripts/requirements-runtime.lock`����Ҫ��Windows���Ŀ�깹����Ҳ��ҪΪ����hash����ر�У�顣�����ļ�ѡ����Ŀ��ƽ̨�����ļ���������������ƽ̨��crcmod������Դ�빹����

����������Ҫ�ؽ������ԡ�©����ѯ��ͬ��third_party/runtime-artifacts.json�������ļ������������ļ�����������Docker��������Դ�빹������������ǰ�����Է���ȫhermetic��

��ָ��Ϊ����׼���壬��δ��ȫ�����˺���ɴ��㸴�֡���֧�ֵ�ǰ��ʵ�ֵ���Ѷ�ƹ��� SCF��˽�� COS �� Cloudflare Pages ·�ߡ����ü����ںŽӿ��ʸ��貿�������к˶ԡ�

## ˳��

1. ȷ���Լ��Ĺ��ں�ӵ������ݸ弰�زĽӿ�Ȩ�ޡ�
2. �������� Python3.10 SCF �¼���������� `scf_probe.main_handler`���ο���ʼ128MiB��90�볬ʱ�������̶��������ڲ�����΢�� IP ������������ԭ�������
3. �� SCF ��̨����΢��ƾ�ݣ�ֻ�� `{"action":"labview.check"}` ��ֻ����顣��ֹ��ӡ�¼���ƾ�ݡ���Ӧ���Ļ����ƣ�OAuth ����ǰ�ر�����/��Ӧ�����ռ���
4. ��������˽�� COS�����ִ�δ�����汾���ơ���ǰʵ��������ֹ����д�룻��ͣ�汾���Ʋ����ڴ�δ�������滻 `deploy/cos-role-policy.json` ��ռλ�˺š�Ͱ�����躯��ִ�н�ɫ������ǰ��ƾ�ݡ�
5. ���ö���������Կ�� CORS ��Դ������ SCF ���� URL���� `$LATEST`�����ò������ݡ���ǰ����ֻ������Ѷ SCF HTTPS ��������֧�������Զ��� API ������
6. ���� Pages��Node22+������ `node scripts/build_pages.mjs`����� `dist/web`����Ҫ��̬����ע���κ����ܡ�����ҳ������д SCF ����ַ����ʵ�� Pages origin ��ȷ���õ���ˡ�
7. ��֤��Ȩ��Ԥ�������桢ˢ�º����¶�ȡ��ͬ�ķ��ظ�������ٿ���΢��ͬ������ȷȷ��һƪ���Ը壬����һ�β����ء�
8. MCP Ϊ��ѡ���룺�����Լ��� App��ʹ���Լ��� `/mcp` ��ַ���˶Ծ�ȷ�ص���ʵ�� scope�������� OAuth��ʾ�� App �ļ�����ֱ�Ӱ�װ����ֹ��ԭ����˽�� App��

## ��������

����̨������ɺ�ChatGPT����뵥����[CHATGPT_PLUGIN.md](CHATGPT_PLUGIN.md)���������գ���ҳ��8��ֻ����ڣ�������������̡̳�

| ���� | �Ƿ����� | ����Ҫ�� |
| --- | --- | --- |
| WECHAT_APP_ID / WECHAT_APP_SECRET | ƾ�ݣ��������� | SCF ��̨���ã���д��Դ���ǰ�� |
| COS_BUCKET | �� | �Լ���˽�й���Ͱ |
| PLATFORM_API_KEY | �� | ����32�ַ����ֵ��������ά���߹���̨ |
| AI_SUBMIT_KEY | �� | ��������32�ַ��������������Կ��ͬ |
| PLATFORM_ALLOWED_ORIGIN | �� | �Լ��� Pages HTTPS origin����ĩβб�� |
| ENABLE_DRAFT_SYNC | �� | ��ʼ�����ã����ֻ����洢��֤����� true |
| MCP_PUBLIC_BASE_URL | �� | �Լ��� SCF HTTPS ����ַ��������ʽ��д |
| MCP_ALLOWED_REDIRECT_URIS | �� | ���Լ����ӽ����ȡ�ľ�ȷ�ص�������ͨ��� |
| MCP_OAUTH_ENABLED | �� | ��־���ص���ƾ�ݸ���˶���ɺ���� true |

�ⲻ�� `.env` �Զ����ػ��ƣ����ں����������������á�COS ����ʱʹ��ִ�н�ɫ��ʱƾ�ݣ�����ƾ��Ӧ������С��Ȩ��

## �ƶ˴�����貹��Ĳ���

��ǰ��ѡû�������Զ��������������ж�����������CI��ʹ��scripts/requirements-runtime.lock�� --require-hashes �ڼ���Linux Python3.10������װ�� `.build/scf-runtime`��������scripts/fetch_chatgpt_oauth.py����ִ��build_scf_probe.py��scripts/check_scf_package.py�����幹�����̼�.github/workflows/check.yml������������ѡ���ƶ�ִ��ͨ����Windowsԭ����������ֱ���ϴ�Linux��

OAuth��ԭ�������粻��ֱ�ӻ�ȡ�ٷ������ͻ���Ԫ����ʱ��ʹ��scripts/fetch_chatgpt_oauth.py�����72Сʱ�Ĺ������ա������߱��밲��ˢ�²��𲢹۲�ʧ�ܣ����ܰ�һ�δ���������ڿ��á���������CI�Ѳ��룻�Զ�����Ȩ��ģ�����Ƽ��𲽽�ͼ�Դ����䡣
