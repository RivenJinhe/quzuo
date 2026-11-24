from app.services import chunks


def test_short_text_is_returned_whole():
    text = "第一段。\n\n第二段。"
    body, total, used = chunks.select(text, 40000, "复习")
    assert body == text
    assert (total, used) == (0, 0)


def test_deep_relevant_chunk_survives_truncation():
    filler = "\n\n".join("这是与任务无关的填充段落，编号 %03d。" % i for i in range(2000))
    key = "温泉需要提前三天预约，现场不再放号。"
    text = filler + "\n\n" + key + "\n\n" + filler
    body, total, used = chunks.select(text, 40000, "周末去泡温泉怎么安排")
    assert len(body) <= 40000
    assert key in body
    assert total > used > 0
    assert len(text) > 80000


def test_head_is_kept_and_order_is_preserved():
    head = "长白山出行指南\n\n范围：北坡、西坡、南坡。"
    middle = "\n\n".join("无关段落 %03d，讲的是别的事情。" % i for i in range(600))
    tail = "温泉需要提前三天预约。"
    body, _, _ = chunks.select(head + "\n\n" + middle + "\n\n" + tail, 12000, "温泉")
    assert body.startswith("长白山出行指南")
    assert body.count("长白山出行指南") == 1
    assert len(body) <= 12000
