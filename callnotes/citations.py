"""Legacy and new transcript references become local paper-like source links."""
import re

BODY = r'S\d{4,}(?:[\s,，、;；\-–—~～至到]+S?\d{4,})*'
BRACKETED = re.compile(r'\[\s*(' + BODY + r')\s*\](?:\([^\n)]*\))?')
BARE = re.compile(r'(?<![A-Za-z0-9_])(' + BODY + r')(?![A-Za-z0-9_])')
REFERENCE = re.compile(BRACKETED.pattern + '|' + BARE.pattern)


def indices_from_label(label):
    matches = list(re.finditer(r'S?(\d{4,})', label))
    result = []
    for position, match in enumerate(matches):
        try:
            value = int(match.group(1))
        except ValueError:
            return []
        if not 1 <= value <= 1000000:
            return []
        if position:
            separator = label[matches[position - 1].end():match.start()]
            previous = int(matches[position - 1].group(1))
            if re.search(r'[-–—~～至到]', separator):
                if value < previous or value - previous > 200:
                    return []
                result.extend(range(previous, value))
        result.append(value - 1)
    return list(dict.fromkeys(result))


def cite_markdown(markdown, revision=0):
    numbers = {}
    matches = list(REFERENCE.finditer(markdown))
    groups = []
    for match in matches:
        indices = indices_from_label(match.group(1) or match.group(2))
        if not indices:
            continue
        if groups and re.fullmatch(r'[ \t,，、;；]*', markdown[groups[-1][-1][0].end():match.start()]):
            groups[-1].append((match, indices))
        else:
            groups.append([(match, indices)])
    pieces, position = [], 0
    for group in groups:
        combined = list(dict.fromkeys(i for _, indices in group for i in indices))
        # A badge opens the whole group that supports this statement. Keep the
        # clicked badge's first source selected, rather than another statement.
        for match, indices in group:
            pieces.append(markdown[position:match.start()])
            number = numbers.setdefault(tuple(indices), len(numbers) + 1)
            target_indices = combined if len(combined) <= 250 else indices
            target = f'source:{int(revision)}:' + ','.join(map(str, target_indices))
            if len(group) > 1 and len(combined) <= 250:
                target += f'?selected={indices[0]}'
            pieces.append(f'[\\[{number}\\]]({target})')
            position = match.end()
    pieces.append(markdown[position:])
    return ''.join(pieces)


def parse_source_url(url):
    revision, indices, _ = parse_source_target(url)
    return revision, indices


def parse_source_target(url):
    match = re.fullmatch(r'source:(\d+):(\d+(?:,\d+)*)(?:\?selected=(\d+))?', url)
    if not match:
        raise ValueError('无效的来源链接')
    indices = list(dict.fromkeys(int(value) for value in match.group(2).split(',')))
    if len(indices) > 250:
        raise ValueError('来源数量超出范围')
    selected = int(match.group(3)) if match.group(3) is not None else indices[0]
    if selected not in indices:
        raise ValueError('选中的来源不在引用列表中')
    return int(match.group(1)), indices, selected
