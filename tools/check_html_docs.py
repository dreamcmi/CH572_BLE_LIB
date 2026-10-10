# -*- coding: utf-8 -*-
"""check_html_docs.py —— 静态检查 docs/html 下的文档。

不引入外部依赖（不装 lxml/bs4），只做能抓真问题的检查：
  1. 文件存在、非空、UTF-8 可解码
  2. 标签基本配对（忽略 void 元素与注释）
  3. 每个 href/src 指向的本地文件存在（外链与锚点跳过）
  4. 每个 <a href="x.html#id"> 的 id 在目标文件里存在
  5. 内联 SVG 的 viewBox 存在（否则图会塌成 0 高）
  6. 没有残留的占位符（TODO / XXX / ???）

用法：
    python tools\\check_html_docs.py
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs', 'html')

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
        'link', 'meta', 'param', 'source', 'track', 'wbr'}


def strip_comments(t):
    return re.sub(r'<!--.*?-->', '', t, flags=re.S)


def check_tags(path, text, errs):
    t = strip_comments(text)
    stack = []
    for m in re.finditer(r'<(/?)([a-zA-Z][\w:-]*)([^>]*?)(/?)>', t):
        closing, name, attrs, selfclose = m.groups()
        name = name.lower()
        if name in VOID or selfclose:
            continue
        if closing:
            if not stack:
                errs.append('%s: 多余的 </%s>' % (os.path.basename(path), name))
                return
            top = stack.pop()
            if top != name:
                errs.append('%s: 标签错配 —— 期望 </%s>，实际 </%s>'
                            % (os.path.basename(path), top, name))
                return
        else:
            stack.append(name)
    if stack:
        errs.append('%s: 未闭合的标签 %s' % (os.path.basename(path), stack))


def check_links(path, text, errs):
    base = os.path.dirname(path)
    for m in re.finditer(r'(?:href|src)\s*=\s*"([^"]+)"', text):
        url = m.group(1).strip()
        if not url or url.startswith(('http://', 'https://', 'mailto:', '#', 'data:')):
            continue
        target, _, frag = url.partition('#')
        if not target:
            continue
        full = os.path.normpath(os.path.join(base, target))
        if not os.path.exists(full):
            errs.append('%s: 链接目标不存在 → %s' % (os.path.basename(path), url))
            continue
        if frag and full.endswith('.html'):
            try:
                other = open(full, encoding='utf-8').read()
            except OSError:
                continue
            if ('id="%s"' % frag) not in other and ("id='%s'" % frag) not in other:
                errs.append('%s: 锚点不存在 → %s' % (os.path.basename(path), url))


def check_svg(path, text, errs):
    for m in re.finditer(r'<svg\b([^>]*)>', text):
        if 'viewBox' not in m.group(1):
            errs.append('%s: <svg> 缺少 viewBox（会塌成 0 高）' % os.path.basename(path))


def check_placeholders(path, text, errs):
    for pat in ('TODO', 'FIXME', 'XXX', '????'):
        if pat in text:
            errs.append('%s: 残留占位符 %s' % (os.path.basename(path), pat))


def check_css(path, text, errs):
    """抓明显的 CSS 语法问题：声明里出现非法的非 ASCII 字符。"""
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith(('/*', '*', '//')):
            continue
        m = re.search(r':\s*([^;{}]+);', line)
        if m:
            val = m.group(1)
            if re.search(r'[\u4e00-\u9fff]', val):
                errs.append('%s:%d: CSS 值里混入中文 → %s'
                            % (os.path.basename(path), i, val.strip()))


def main():
    if not os.path.isdir(DOCS):
        print('找不到目录 %s' % DOCS)
        return 1

    errs = []
    files = sorted(f for f in os.listdir(DOCS)
                   if f.endswith(('.html', '.css')))
    if not files:
        print('%s 下没有任何 html/css' % DOCS)
        return 1

    for name in files:
        path = os.path.join(DOCS, name)
        raw = open(path, 'rb').read()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as e:
            errs.append('%s: 不是合法 UTF-8（%s）' % (name, e))
            continue
        if not text.strip():
            errs.append('%s: 文件为空' % name)
            continue
        if name.endswith('.html'):
            check_tags(path, text, errs)
            check_links(path, text, errs)
            check_svg(path, text, errs)
            check_placeholders(path, text, errs)
        else:
            check_css(path, text, errs)
        print('  [检查] %-18s %6d 字节' % (name, len(raw)))

    print()
    if errs:
        print('发现 %d 个问题：' % len(errs))
        for e in errs:
            print('   - ' + e)
        return 1
    print('全部通过：%d 个文件，标签配对 / 链接有效 / SVG 完整' % len(files))
    return 0


if __name__ == '__main__':
    sys.exit(main())
