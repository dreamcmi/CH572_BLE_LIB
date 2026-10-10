# -*- coding: utf-8 -*-
"""Validate the generated MRS project structure and selected build mode."""
import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


DRIVERS = {
    "CH57x_clk.c",
    "CH57x_flash.c",
    "CH57x_gpio.c",
    "CH57x_sys.c",
    "CH57x_uart.c",
    "CH57x_pwr.c",
}
LINKS = {"ble_include", "ble_hal", "ble_lib", "ble_src", "ble_debug", "ble_rom"}


def option(root, suffix):
    matches = [node for node in root.iter("option") if node.get("id", "").endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one option ending in {suffix!r}, found {len(matches)}")
    return matches[0]


def values(node):
    return [entry.get("value", "").strip().strip('"') for entry in node.findall("listOptionValue")]


def require(condition, message, failures):
    print(f"  [{'OK' if condition else 'FAIL'}] {message}")
    if not condition:
        failures.append(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cproject", type=Path)
    parser.add_argument("--project", type=Path, help=".project file; defaults to sibling .project")
    parser.add_argument("--mode", choices=("rom", "static"), default="rom")
    parser.add_argument("--debug", action="store_true", help="expect the DEBUG define")
    args = parser.parse_args()
    project_path = args.project or args.cproject.with_name(".project")
    root_dir = Path(__file__).resolve().parents[1]
    failures = []

    try:
        croot = ET.parse(args.cproject).getroot()
        proot = ET.parse(project_path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"[FAIL] XML parse: {exc}", file=sys.stderr)
        return 2

    print(f"MRS config: {args.cproject}")
    print(f"Mode: {args.mode}; DEBUG: {'on' if args.debug else 'off'}")
    config = croot.find(".//configuration")
    require(config is not None, "one active build configuration", failures)
    if config is None:
        return 2

    try:
        includes = set(values(option(croot, "option.c.compiler.include.paths.1567947810")))
        asm_includes = set(values(option(croot, "option.assembler.include.paths.1034038285")))
        cdefs = set(values(option(croot, "option.c.compiler.defs.177116515")))
        asmdefs = set(values(option(croot, "option.assembler.defs.181380015")))
        link_paths = set(values(option(croot, "option.c.linker.paths.2057340378")))
        libs = set(values(option(croot, "option.c.linker.libs.1350862782")))
        linker_script = values(option(croot, "option.c.linker.scriptfile.1390103472"))
    except ValueError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    expected_common = {
        "${workspace_loc:/${ProjName}}",
        "${workspace_loc:/${ProjName}/include}",
        "${workspace_loc:/${ProjName}/ble_include}",
        "${workspace_loc:/${ProjName}/ble_src/StdPeriphDriver}",
        "${workspace_loc:/${ProjName}/ble_src/RVMSIS}",
    }
    expected_includes = expected_common | (
        {"${workspace_loc:/${ProjName}/ble_rom}"} if args.mode == "rom" else set()
    )
    require(expected_includes <= includes, "C compiler include paths match the app/SDK/mode", failures)
    require(expected_includes <= asm_includes, "assembler include paths match the app/SDK/mode", failures)

    expected_defs = ({"CH572BLE_ROM=1"} if args.mode == "rom" else set())
    if args.debug:
        expected_defs.add("DEBUG")
    require(cdefs == expected_defs, f"C defines are exactly {sorted(expected_defs)}", failures)
    require(asmdefs == expected_defs, f"assembler defines are exactly {sorted(expected_defs)}", failures)

    expected_script = "${workspace_loc:/${ProjName}/ble_src/Ld/Link_rom.ld}" if args.mode == "rom" else "${workspace_loc:/${ProjName}/ble_src/Ld/Link.ld}"
    require(linker_script == [expected_script], f"linker script is {expected_script}", failures)
    expected_link_paths = {
        "${workspace_loc:/${ProjName}/ble_lib}",
        "${workspace_loc:/${ProjName}/ble_rom}",
    }
    require(expected_link_paths <= link_paths, "ble_lib and ROM metadata linker search paths are present", failures)

    entries = {entry.get("name"): entry for entry in croot.findall(".//sourceEntries/entry")}
    if args.mode == "rom":
        require(not ({"CH572BLE", "ISP572"} & libs), "ROM app does not link BLE/ISP archives", failures)
        require("ble_hal" in entries, "ROM app compiles HAL sources", failures)
    else:
        require(libs == {"CH572BLE", "ISP572"}, "static app links only CH572BLE and ISP572", failures)
        require("ble_hal" not in entries, "static app does not compile duplicate HAL sources", failures)

    required_entries = {"src", "ble_src/Startup", "ble_debug"}
    if not (args.cproject.parent / ".nostdperiph").exists():
        required_entries.add("ble_src/StdPeriphDriver")
    if args.mode == "rom":
        required_entries.add("ble_hal")
    require(set(entries) == required_entries, f"source entries are exactly {sorted(required_entries)}", failures)

    main_excluded = "main.c" if args.mode == "rom" else "main_rom.c"
    require(main_excluded in entries.get("src", {}).get("excluding", "").split("|"), f"src excludes {main_excluded}", failures)
    if "ble_src/StdPeriphDriver" in entries:
        excluded = set(entries["ble_src/StdPeriphDriver"].get("excluding", "").split("|"))
        driver_dir = root_dir / "src" / "StdPeriphDriver"
        candidates = {item.name for item in driver_dir.glob("*.c")}
        present = candidates & DRIVERS
        require(present == DRIVERS, "all six build.ps1 drivers exist", failures)
        require(not (DRIVERS & excluded), "selected build.ps1 drivers are not excluded", failures)
        require((candidates - DRIVERS) <= excluded, "all other StdPeriphDriver C files are excluded", failures)

    artifact = config.get("artifactName", "")
    expected_artifact = args.cproject.parent.name + ("-rom" if args.mode == "rom" else "") + ("-debug" if args.debug else "")
    require(artifact == expected_artifact, f"artifact name is {expected_artifact}", failures)
    builder = croot.find(".//builder")
    require(builder is not None and builder.get("buildPath") == "${workspace_loc:/${ProjName}/obj}", "MRS objects build under this project's obj directory", failures)

    linked_nodes = proot.findall(".//linkedResources/link")
    linked = {link.findtext("name") for link in linked_nodes}
    require(LINKS <= linked, f".project links include {sorted(LINKS)}", failures)
    locations = {link.findtext("name"): link.findtext("locationURI") for link in linked_nodes}
    expected_locations = {
        "ble_include": "PARENT-2-PROJECT_LOC/include",
        "ble_hal": "PARENT-2-PROJECT_LOC/hal",
        "ble_lib": "PARENT-2-PROJECT_LOC/lib",
        "ble_src": "PARENT-2-PROJECT_LOC/src",
        "ble_debug": "PARENT-2-PROJECT_LOC/debug",
        "ble_rom": "PARENT-2-PROJECT_LOC/rom",
    }
    require(all(locations.get(name) == path for name, path in expected_locations.items()), ".project linked resource locations resolve to SDK folders", failures)
    require((proot.findtext("name") or "") == args.cproject.parent.name, ".project name matches its directory", failures)

    if failures:
        print(f"\nResult: FAIL ({len(failures)} issue(s))")
        return 2
    print("\nResult: PASS")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
