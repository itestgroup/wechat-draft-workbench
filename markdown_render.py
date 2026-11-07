"""Small escaped Markdown renderer for WeChat; no raw HTML or remote images."""
import html
import re
from urllib.parse import urlsplit

VERSION = 3
TOKEN = re.compile(r'`([^`]+)`|(?<!!)\[([^\]]+)\]\((https?://[^\s)]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*')


def inline(text, depth=0):
    if depth > 4:
        return html.escape(text)
    parts, cursor = [], 0
    for match in TOKEN.finditer(text):
        parts.append(html.escape(text[cursor:match.start()]))
        code, label, url, bold, italic = match.groups()
        if code is not None:
            parts.append('<code style="background:#eef3f7;padding:2px 4px">' + html.escape(code) + '</code>')
        elif label is not None:
            target = urlsplit(url)
            if target.hostname and not target.username and not target.password and not any(ord(c) < 32 for c in url):
                parts.append('<a style="color:#195b87" href="' + html.escape(url, quote=True) + '">' + html.escape(label) + '</a>')
            else:
                parts.append(html.escape(match.group()))
        elif bold is not None:
            parts.append('<strong>' + inline(bold, depth+1) + '</strong>')
        else:
            parts.append('<em>' + inline(italic, depth+1) + '</em>')
        cursor = match.end()
    return ''.join(parts) + html.escape(text[cursor:])


def cells(line):
    return [part.strip() for part in line.strip().strip('|').split('|')]


def render_markdown(source):
    lines, output, paragraph = source.splitlines(), [], []
    def flush():
        if paragraph:
            output.append('<p style="line-height:1.9;margin:16px 0">' + '<br>'.join(inline(s) for s in paragraph) + '</p>')
            paragraph.clear()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith('```'):
            flush(); index += 1; code = []
            while index < len(lines) and not lines[index].startswith('```'):
                code.append(lines[index]); index += 1
            output.append('<pre style="white-space:pre-wrap;background:#f2f5f8;padding:16px;font-size:14px"><code>' + html.escape('\n'.join(code)) + '</code></pre>')
        elif re.fullmatch(r'!\[([^\]]*)\]\(asset:([0-9a-f]{64})\)', line.strip()):
            flush()
            match = re.fullmatch(r'!\[([^\]]*)\]\(asset:([0-9a-f]{64})\)', line.strip())
            output.append('<p><img data-image-id="'+match.group(2)+'" style="width:100%;max-width:900px;height:auto" alt="'+html.escape(match.group(1),quote=True)+'"></p>')
        elif '|' in line and index+1 < len(lines) and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells(lines[index+1])):
            flush(); headings = cells(line); rows = []; index += 2
            while index < len(lines) and '|' in lines[index] and lines[index].strip():
                rows.append(cells(lines[index])); index += 1
            table = '<table style="width:100%;border-collapse:collapse;font-size:14px"><thead><tr>'
            style = ' style="border:1px solid #ccd8e2;padding:8px;text-align:left"'
            table += ''.join('<th'+style+'>'+inline(cell)+'</th>' for cell in headings) + '</tr></thead><tbody>'
            for row in rows:
                table += '<tr>'+''.join('<td'+style+'>'+inline(cell)+'</td>' for cell in (row+['']*len(headings))[:len(headings)])+'</tr>'
            output.append(table+'</tbody></table>'); continue
        elif re.match(r'^\s*(?:[-+*]|\d+\.)\s+', line):
            flush(); ordered = bool(re.match(r'^\s*\d+\.', line)); tag = 'ol' if ordered else 'ul'
            pattern = r'^\s*\d+\.\s+(.*)' if ordered else r'^\s*[-+*]\s+(.*)'
            items = []
            while index < len(lines):
                match = re.match(pattern, lines[index])
                if not match: break
                items.append('<li style="margin:8px 0">'+inline(match.group(1))+'</li>'); index += 1
            output.append('<'+tag+' style="padding-left:24px;line-height:1.9">'+''.join(items)+'</'+tag+'>'); continue
        elif line.startswith('> '):
            flush(); quotes = []
            while index < len(lines) and lines[index].startswith('> '):
                quotes.append(inline(lines[index][2:])); index += 1
            output.append('<blockquote style="border-left:4px solid #195b87;background:#f2f6f9;padding:12px;margin:16px 0">'+'<br>'.join(quotes)+'</blockquote>'); continue
        elif re.fullmatch(r'\s*(?:---+|\*\*\*+)\s*', line):
            flush(); output.append('<hr style="border:0;border-top:1px solid #dbe4eb;margin:24px 0">')
        elif re.match(r'^#{1,6} ', line):
            flush(); level = len(line.split(' ',1)[0])
            output.append('<h%d style="color:#195b87;margin:24px 0 12px">%s</h%d>' % (level,inline(line[level+1:]),level))
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
        index += 1
    flush()
    return '<section style="font-size:16px;color:#263445">'+''.join(output)+'</section>'
