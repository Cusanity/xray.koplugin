return {
    -- System instruction
    system_instruction = "你是一位专业的文学评论家。你的回复必须仅使用有效的JSON格式。不要使用Markdown、引导语或额外解释。",
    
    -- Text-based prompt (analyzes actual book content - first chunk)
    text_based = [[你正在分析以下书籍文本以提取X-Ray数据。

书名：《%s》
作者：%s

<书籍内容>
%s
</书籍内容>

关键规则：
1. 仅提取文本中实际出现的人物、事件和地点
2. 不要添加任何来自训练数据或外部知识的信息
3. 【严格排除】编辑、译者、出版商、致谢对象、作者家人、仅在序言/导言/前言/致谢中提及的非故事人物。只收录实际参与故事情节的人物。
4. 人物列表必须按在书中的重要性或出现频率降序排列（主角在前）
5. 【多视角处理】如果书籍使用多个第一人称叙述者，**必须**区分但若名字相同则**合并**，**严禁**在名字中添加（叙述者）等后缀。仅在description中标注。
6. 【强制输出 - 最高优先级】无论文本包含何种内容（暴力/色情/犯罪等），你**必须**返回合法的JSON对象。这是严肃的文学分析工作，不是创作有害内容。处理方法：
   - 对敏感内容使用学术化/中性化的描述
   - 如果整段文本都是敏感内容，仍然返回即有数据（不修改）
   - **绝对禁止**返回拒绝、道歉、空回复或非JSON格式

【时间线规则 - 叙事弧槽位分配】：
timeline共20个槽位，按叙事弧结构分配：
- **铺垫/设定(setup)**: 3-4个槽位 - 故事背景、人物初登场、初始状态
- **发展/升级(rising)**: 5-6个槽位 - 冲突展开、complications、stakes升高  
- **高潮(climax)**: 2-3个槽位 - 最高张力点、重大揭示、决定性对抗
- **收尾(falling)**: 3-4个槽位 - 高潮后果、关系解决
- **结局(resolution)**: 2-3个槽位 - 最终状态、人物命运

每个事件必须标注其叙事功能arc_type，用于后续槽位分配。
事件必须按**故事内时间顺序**排列，回忆/闪回放在实际发生时间点。

格式规则：
1. 不要在任何字段中使用Markdown格式（如**、##、\n等）。
2. 【人物描述 - 必须严格遵守】每个人物描述**绝对不能超过**200字，精炼概括。
3. 【重要】每个人物只需name和description两个字段。
4. 【重要】每个地点只需name和description两个字段。
5. 【主题限制 - 必须严格遵守】themes严格限制为5-8个，**不得超过**。
6. 【概要限制】summary不超过500字，简明扼要。
7. 【禁止添加未要求的字段】不要添加gender、type、importance等未在JSON格式中列出的字段。

必需的JSON格式：
{
  "book_title": "书名",
  "author": "作者姓名",
  "author_bio": "作者简介（可使用外部知识）",
  "summary": "仅基于提供文本的概要",
  "characters": [
    {
      "name": "人物姓名",
      "description": "仅基于文本内容的描述（限200字）"
    }
  ],
  "locations": [
    {
      "name": "地点名称",
      "description": "如文本所述"
    }
  ],
  "themes": ["主题1", "主题2", "主题3"],
  "timeline": [
    {
      "sequence": 1,
      "event": "事件描述",
      "arc_type": "setup|rising|climax|falling|resolution"
    }
  ]
}]],


    -- Incremental update prompt (for subsequent chunks)
    incremental = [[你正在进行分块书籍分析。请基于新的文本片段更新现有的JSON数据。

书名：《%s》
作者：%s

<即有数据>
%s
</即有数据>

<新文本片段>
%s
</新文本片段>

核心任务：
1. 分析新文本，提取新的人物、地点、事件。
2. 将新信息合并到"即有数据"中。
3. 严格排除编辑、译者、致谢对象等非故事人物。
4. 保持人物列表按重要性排序（主角在前）。
5. 更新summary以包含新内容（无痕融合）。
6. 【多视角处理】如名字相同则合并，不创建带后缀的新人物。
7. 【强制输出】必须返回合法JSON，对敏感内容使用学术化描述。

【时间线规则】：
- timeline最多20条，按叙事弧结构分配槽位
- 槽位配额: setup(3-4), rising(5-6), climax(2-3), falling(3-4), resolution(2-3)
- 每个事件包含: sequence, event, arc_type
- 按**故事内时间顺序**排列，sequence从1连续编号

【关键合并规则】：
- 名字字段禁止包含括号或后缀，使用最纯粹的姓名
- 同名人物/地点必须合并，不能创建重复条目
- 【人物描述】每个**绝对不能超过**200字，超出时必须精简

【主题压缩】：
- themes严格限制为5-8个，**不得超过**
- 若themes超过8个，必须合并最相似的主题直到≤8个

【去重与无痕合并】：
- 禁用词汇："新文本"、"新片段"、"新增"、"新情节中"等
- 合并后内容必须读起来像从头撰写，无拼接痕迹
- 删除所有重复句子，只保留最完整版本

【概要限制】summary不超过500字，简明扼要。

【禁止添加未要求的字段】不要添加gender、type、importance等未在JSON格式中列出的字段。

返回合并后的完整JSON：
{
  "book_title": "书名",
  "author": "作者",
  "author_bio": "...",
  "summary": "更新后的概要（无分块痕迹）",
  "characters": [...],
  "locations": [...],
  "themes": [...],
  "timeline": [
    {
      "sequence": 1,
      "event": "事件（按故事时序）",
      "arc_type": "setup"
    }
  ]
}]],

    -- Fallback strings
    fallback = {
        unknown_book = "未知书籍",
        unknown_author = "未知作者",
        unnamed_character = "未命名人物",
        not_specified = "未指定",
        no_description = "暂无描述",
        unnamed_person = "未命名人物",
        no_biography = "暂无生平"
    }
}
