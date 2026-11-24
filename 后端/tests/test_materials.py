import pytest
from pydantic import ValidationError

from app.schemas import MaterialCreate, MaterialUpdate, material_status


def test_link_can_be_saved_without_an_excerpt():
    material = MaterialCreate(
        title="周末展览",
        topic="出行",
        source_kind="web",
        source_url="https://example.org/exhibition",
    )

    assert material.excerpt == ""
    assert material.status == "link_only"


def test_adding_an_excerpt_makes_a_link_citable():
    update = MaterialUpdate(version=2, excerpt="展览下午四点结束。")

    assert material_status(update.excerpt, "https://example.org/exhibition") == "citable"


def test_unreadable_file_can_be_saved_with_its_filename():
    material = MaterialCreate(title="扫描讲义", topic="复习", source_kind="courseware", source_file="扫描讲义.pdf")

    assert material.status == "unusable"


def test_whitespace_cannot_be_used_as_a_citable_excerpt():
    with pytest.raises(ValidationError, match="必须提供摘录"):
        MaterialCreate(title="课件", topic="复习", excerpt="  \n", status="citable")
