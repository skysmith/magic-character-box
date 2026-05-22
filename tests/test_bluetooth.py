import subprocess
import unittest

from magic_box.bluetooth import BluetoothController, normalize_bluetooth_address


class BluetoothControllerTests(unittest.TestCase):
    def test_status_parses_adapter_devices_and_default_sink(self) -> None:
        controller = BluetoothController(
            bluetoothctl="/usr/bin/bluetoothctl",
            pactl="/usr/bin/pactl",
            wpctl="/usr/bin/wpctl",
            runner=_runner_for_status,
        )

        status = controller.status()

        self.assertTrue(status.available)
        self.assertTrue(status.powered)
        self.assertEqual(status.default_sink, "Sony Speaker")
        self.assertEqual(len(status.devices), 1)
        device = status.devices[0]
        self.assertEqual(device.address, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(device.name, "Sony Speaker")
        self.assertTrue(device.paired)
        self.assertTrue(device.trusted)
        self.assertTrue(device.connected)
        self.assertTrue(device.audio)

    def test_use_for_audio_connects_and_sets_matching_pulse_sink(self) -> None:
        calls: list[list[str]] = []

        def runner(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            if args[1:3] == ["list", "short"]:
                return _completed(args, "54\tbluez_output.AA_BB_CC_DD_EE_FF.1\tPipeWire\tfloat32le\n")
            if args[1] == "set-default-sink":
                return _completed(args)
            if args[1] == "info":
                return _completed(args, _device_info(paired="no"))
            return _runner_for_status(args)

        controller = BluetoothController(
            bluetoothctl="/usr/bin/bluetoothctl",
            pactl="/usr/bin/pactl",
            wpctl="/usr/bin/wpctl",
            runner=runner,
        )

        result = controller.use_for_audio("aa:bb:cc:dd:ee:ff")

        self.assertTrue(result.ok)
        self.assertTrue(any(call[-2:] == ["pair", "AA:BB:CC:DD:EE:FF"] for call in calls))
        self.assertTrue(any(call[-2:] == ["trust", "AA:BB:CC:DD:EE:FF"] for call in calls))
        self.assertTrue(any(call[-2:] == ["connect", "AA:BB:CC:DD:EE:FF"] for call in calls))
        self.assertTrue(any("set-default-sink" in call for call in calls))

    def test_normalize_bluetooth_address_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            normalize_bluetooth_address("../../not-a-device")


def _runner_for_status(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
    if args[1] == "show":
        return _completed(args, "Controller 11:22:33:44:55:66\n\tPowered: yes\n\tDiscovering: no\n")
    if args[1] == "devices":
        return _completed(args, "Device AA:BB:CC:DD:EE:FF Sony Speaker\n")
    if args[1] == "info":
        return _completed(args, _device_info(paired="yes"))
    if args[1] in {"pair", "trust", "connect", "disconnect", "power", "scan"}:
        return _completed(args)
    if args[1] == "inspect":
        return _completed(args, 'node.description = "Sony Speaker"\n')
    return _completed(args)


def _completed(args: list[str], stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def _device_info(paired: str) -> str:
    return "\n".join(
        [
            "Device AA:BB:CC:DD:EE:FF",
            "\tName: Sony Speaker",
            "\tIcon: audio-card",
            f"\tPaired: {paired}",
            "\tTrusted: yes",
            "\tConnected: yes",
            "\tUUID: Audio Sink",
        ]
    )


if __name__ == "__main__":
    unittest.main()
