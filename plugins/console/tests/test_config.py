from copy import deepcopy
import pytest
from helpers import slice_for


def validate(data):
    from gear_console.config import validate_slice

    return validate_slice(data)


def test_missing_hardware_is_optional_no_placeholders():
    assert (
        validate({"plugin": {"id": "gear.console", "config": {}}, "resources": {}})[
            "status"
        ]
        == "VALID"
    )
    data = slice_for()
    before = deepcopy(data)
    assert validate(data)["status"] == "VALID"
    assert data == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", "COM0"),
        ("port", "file.txt"),
        ("baudrate", True),
        ("baudrate", 0),
        ("role", "OTHER"),
        ("parity", "X"),
        ("stopbits", 3),
        ("data_bits", 7),
        ("encoding", "ascii"),
        ("line_ending", "\\n"),
        ("unknown", 1),
    ],
)
def test_rejects_invalid_private_settings(field, value):
    data = slice_for(**{field: value})
    assert validate(data)["status"] == "INVALID"


@pytest.mark.parametrize("missing", ["device", "port", "role"])
def test_unfinished_binding_is_incomplete(missing):
    data = slice_for()
    record = data["resources"]["CONSOLE.mcu"]
    (record if missing == "device" else record["config"]).pop(missing)
    assert validate(data)["status"] == "INCOMPLETE"


@pytest.mark.parametrize(
    "port,role,device,code",
    [
        ("com77", "SOC", "ADB002", "CONSOLE_PORT_DUPLICATE"),
        ("COM78", "MCU", "ADB001", "CONSOLE_ROLE_DUPLICATE"),
    ],
)
def test_exclusive_physical_port_and_board_role(port, role, device, code):
    data = slice_for()
    data["resources"]["CONSOLE.second"] = {
        "type": "CONSOLE",
        "device": device,
        "config": {"port": port, "role": role},
    }
    report = validate(data)
    assert report["status"] == "INVALID"
    assert code in {d["code"] for d in report["diagnostics"]}


def test_user_shortcuts_are_validated_without_invented_commands():
    data = slice_for()
    data["plugin"]["config"]["shortcuts"] = [
        {"label": "My command", "command": "user chosen"}
    ]
    assert validate(data)["status"] == "VALID"
    data["plugin"]["config"]["shortcuts"][0]["command"] = 3
    assert validate(data)["status"] == "INVALID"
