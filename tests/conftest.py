import pytest

from catalog.seed import ensure_seed


@pytest.fixture
def client():
    from django.test import Client
    return Client()


@pytest.fixture
def seeded_db(db):
    """统一播种：保证读盘任务、水位尺与源文件行齐全。"""
    ensure_seed()
