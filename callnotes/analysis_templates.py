"""Explicit user-selected analysis directions; evidence rules stay in force."""

TEMPLATES = {
    'topics': ('话题整理', '按话题梳理双方表达与未说清的地方。', None),
    'friends': ('朋友闲聊', '像回顾聊天一样自然，不强行分析客户需求或待办。',
        '请回顾这次朋友间的闲聊。按实际聊到的话题说明大家各自说了什么，保留有趣的细节、观点和分歧。'
        '语气自然，避免商务报告口吻。不强行列需求、顾虑、待办，不推测性格或关系。没有明确约定就不用写约定。'),
    'customer': ('客户需求', '聚焦明确需求、预算、顾虑和仍需问清的问题。',
        '请从客户沟通角度整理本次通话：明确需求、预算和价格顾虑、限制条件、已明确的约定、仍待问清的问题。'
        '严格区分原话支持的事实与待验证的推测。不把闲聊包装成购买意向；无法确定客户身份时说明，不擅自归属。'),
    'review': ('沟通复盘', '回顾表达是否清楚、误解和可以改善的说法。',
        '请复盘本次沟通。先说明讨论的重点，再找双方是否存在未回答的问题、表述歧义或误解。'
        '仅在原话能支持时提出具体的表达改进建议，建议单独标明，不能当作已经说过的话。不猜测真实心理。'),
    'agreements': ('约定核对', '提取明确说好的事项，区分提议与承诺。',
        '请核对本次对话中明确达成的约定，列出事项、原话明确的负责人、时间与条件。'
        '区分提议、单方意愿和双方确认。没提到的人名或日期写未明确，不补全。没有约定就直接说明。'),
}


def report_prompt(key, default):
    label, _, prompt = TEMPLATES.get(key, TEMPLATES['topics'])
    if prompt is None:
        return default
    return ('【分析模板：' + label + '】\n' + prompt +
            '\n使用简体中文、简明标题和短段落；关键结论附 [S0001] 形式的来源标记。')


def is_template_report(question):
    return any(question == report_prompt(key, '') for key in TEMPLATES if key != 'topics')


def question_prompt(key, question):
    if key not in TEMPLATES or key == 'topics':
        return question
    label, hint, _ = TEMPLATES[key]
    return f'【分析方向：{label}】\n{hint}\n优先回答我的具体问题，不强行套用报告结构。\n【我的问题】\n{question}'


def question_label(question):
    if question.startswith('【分析方向：') and '\n【我的问题】\n' in question:
        return question.split('\n【我的问题】\n', 1)[1]
    return question
