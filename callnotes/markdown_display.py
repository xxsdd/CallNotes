"""Repair malformed emphasis for display without changing stored AI answers."""
import html
import re
import unicodedata

STRONG = re.compile(r'(?<![\\*])\*\*(?!\*)([^\n*]+?)(?<!\\)\*\*(?!\*)')
INLINE_CODE = re.compile(r'(?<!`)(`+)(?!`)(.*?)\1(?!`)', re.DOTALL)
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')


def readable_markdown(markdown):
    """Accept AI labels such as **各方表达： **内容 that Qt leaves literal.

    Leave code, escaped markers and ordinary valid Markdown alone. HTML strong
    avoids punctuation-adjacent Chinese emphasis ambiguity without adding spaces
    to the actual words. Stored answers and their source IDs are never rewritten.
    """
    def repair(text):
        def replace(match):
            raw = match.group(1)
            label = raw.strip()
            following = text[match.end():match.end() + 1]
            malformed = raw != label or (label and following and following.isalnum()
                and unicodedata.category(label[-1]).startswith('P'))
            if not malformed or not label or any(c in label for c in '<>[]`_'):
                return match.group(0)
            return '<strong>' + html.escape(label) + '</strong>'
        return STRONG.sub(replace, text)

    output, fence = [], None
    for line in markdown.splitlines(keepends=True):
        marker = FENCE.match(line)
        if fence:
            output.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if marker:
            fence = marker[1]
            output.append(line)
            continue
        # Indented Markdown code is also literal.
        if line.startswith(('    ', '\t')):
            output.append(line)
            continue
        parts, cursor = [], 0
        for code in INLINE_CODE.finditer(line):
            parts.extend((repair(line[cursor:code.start()]), code.group(0)))
            cursor = code.end()
        parts.append(repair(line[cursor:]))
        output.append(''.join(parts))
    return ''.join(output)
