"""对抗面薄封装（功能 020 / T2086，C-01）：把本特性的对抗场景**纳入既有合并阻塞门禁**。

**薄封装纪律**：本文件**不复制第二份断言逻辑**——它只做两件事：

1. 用 `pytest` 的夹具解析把 **unit/contract 侧既有用例体**（或其共享夹具）取出来**原样调用**
   （`_call` 只负责 `request.getfixturevalue` 与实例化，不断言任何业务口径）；
2. 打 `@pytest.mark.adversarial`，使这些场景随 `uv run pytest tests/adversarial -m adversarial`
   （CI 合并阻塞门禁，T2081 由父代理执行）一起跑。

细粒度断言本体仍在 unit/contract 侧（`tests/unit/test_promo_daily_ingest.py` /
`test_billing_channels.py` / `test_promo_delivery_gate.py` / `test_calibration_transfer.py` /
`tests/contract/test_transfer_contracts.py`），本文件是**纳入口**：篡改、伪造、绕过一旦成功，
这里与 unit 侧同时变红。

覆盖的对抗场景（与 T2086③ 逐条对应）：

- 伪造 `promo_daily_metrics` 行（自造 `period` / 越域 `source`）；
- 改写已落盘日级记录与快照（存储层 INSERT-only 触发器）；
- 伪造 `covered_days`（自造归属日、模拟冒充真实）；
- **门禁绕过**（伪造 `--tier`/`stage`、不传 guard 的投放装配、投放面第二处包装点）；
- 迁移件系统字段改写与省略（`system_digest` 机检 + append-only）、不可迁移件被误采纳；
- 媒体渠道账单篡改后再对账（未解释项 100% 告警）。
"""

import importlib

import pytest

pytestmark = pytest.mark.adversarial

_ADVERSARIAL_DAILY = [
    # 伪造日级行：同（活动, 周期）重复回流 / 越域来源 / 缺来源原因
    ("tests.unit.test_promo_daily_ingest", "Test唯一性与多日合法", "test_幂等拒绝且整批不中断"),
    ("tests.unit.test_promo_daily_ingest", "Test来源纪律", "test_域外来源拒绝且零落盘"),
    ("tests.unit.test_promo_daily_ingest", "Test来源纪律", "test_回落无原因即拒绝"),
    # 改写已落盘记录：存储层 UPDATE/DELETE 一律抛错
    ("tests.unit.test_promo_daily_ingest", "Test存储层拒改写", "test_update_delete_一律抛错"),
    # 伪造 covered_days：模拟日不进覆盖；全模拟不达标
    (
        "tests.unit.test_promo_daily_ingest",
        "Test来源纪律",
        "test_模拟日不进覆盖_十五天全模拟不达标",
    ),
    ("tests.unit.test_promo_daily_ingest", "Test来源纪律", "test_达标且全真实才允许宣称达成"),
    # 门禁绕过：未声明投放环节 / 唯一包装点 / 两腿不得互相替代
    (
        "tests.unit.test_promo_delivery_gate",
        "Test调用前拒绝",
        "test_未声明投放环节即_tier_undeclared",
    ),
    ("tests.unit.test_promo_delivery_gate", "Test两腿共同生效", "test_两道不得互相替代_静态"),
    ("tests.unit.test_promo_delivery_gate", "Test两腿共同生效", "test_唯一包装点_静态"),
    ("tests.unit.test_promo_delivery_gate", "Test分型互斥", "test_三类错误类型互斥"),
    # 模拟冒充真实：source 只能由装配面声明；覆盖只计 real
    ("tests.unit.test_billing_channels", "TestC18诚实分层", "test_source_只能由装配面声明"),
    (
        "tests.unit.test_billing_channels",
        "TestC18诚实分层",
        "test_覆盖只计_real_且结论文案取值域二元素",
    ),
    # 迁移件：系统字段改写 / 省略 / 不可迁移件被误采纳
    ("tests.unit.test_calibration_transfer", "Testappend_only", "test_系统字段改写即拒采信"),
    ("tests.unit.test_calibration_transfer", "Testappend_only", "test_省略系统字段即拒采信"),
    ("tests.unit.test_calibration_transfer", "Testappend_only", "test_同键重产拒绝"),
    (
        "tests.unit.test_calibration_transfer",
        "Test人工两键与误迁移恒零",
        "test_不可迁移件不得采纳",
    ),
    (
        "tests.unit.test_calibration_transfer",
        "Test人工两键与误迁移恒零",
        "test_终态不可逆",
    ),
    # 迁移面：件被改写后报表拒采信（contract 侧复核）
    (
        "tests.contract.test_transfer_contracts",
        "TestC17CLI与退出码",
        "test_报表件被改写即拒采信退出一",
    ),
]


def _resolve(name: str, request, owner):
    """解析夹具：先经 pytest（conftest 面），再回落到**用例所在模块**的局部夹具定义。

    回落的必要性：`daily_scene` / `daily_config` 等夹具定义在 `tests/unit/*.py` 内部，
    本文件（对抗纳入口）看不到它们——薄封装据此仍**只调用既有夹具函数**，不复制其构造逻辑。
    """
    import inspect

    try:
        return request.getfixturevalue(name)
    except Exception:  # noqa: BLE001 - 夹具不在 conftest 面 ⇒ 走模块局部定义
        factory = getattr(owner, name, None)
        if factory is None:
            raise
        params = [param for param in inspect.signature(factory).parameters if param != "self"]
        kwargs = {param: _resolve(param, request, owner) for param in params}
        # 取未包装的原函数（pytest 禁止直接调用夹具包装对象）
        raw = getattr(factory, "__wrapped__", factory)
        return raw(**kwargs)


def _call(module_name: str, class_name: str, method_name: str, request) -> None:
    """调用 unit/contract 侧既有用例体（夹具按名解析；**不在本文件重写断言**）。"""
    import inspect

    module = importlib.import_module(module_name)
    case = getattr(module, class_name)()
    method = getattr(case, method_name)
    params = list(inspect.signature(method).parameters)
    if params and params[0] == "self":
        params = params[1:]
    kwargs = {name: _resolve(name, request, module) for name in params}
    method(**kwargs)


@pytest.mark.parametrize(
    "module_name,class_name,method_name", _ADVERSARIAL_DAILY, ids=lambda value: value[-32:]
)
def test_020对抗面纳入合并阻塞门禁(module_name, class_name, method_name, request):
    """逐条调用 unit/contract 侧的对抗断言本体（薄封装，零第二份断言逻辑）。"""
    _call(module_name, class_name, method_name, request)


def test_020对抗面覆盖清单常驻(request):
    """清单本身是**阻塞面**的一部分：删掉任一条目即红（不得静默缩小对抗覆盖）。"""
    assert len(_ADVERSARIAL_DAILY) == 18
    covered = {(module, class_name) for module, class_name, _ in _ADVERSARIAL_DAILY}
    assert {
        ("tests.unit.test_promo_daily_ingest", "Test存储层拒改写"),
        ("tests.unit.test_promo_daily_ingest", "Test来源纪律"),
        ("tests.unit.test_promo_delivery_gate", "Test调用前拒绝"),
        ("tests.unit.test_promo_delivery_gate", "Test两腿共同生效"),
        ("tests.unit.test_billing_channels", "TestC18诚实分层"),
        ("tests.unit.test_calibration_transfer", "Testappend_only"),
        ("tests.unit.test_calibration_transfer", "Test人工两键与误迁移恒零"),
        ("tests.contract.test_transfer_contracts", "TestC17CLI与退出码"),
    } <= covered
