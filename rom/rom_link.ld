/* Fixed-address CH572 BLE image. RAM code has a Flash load image.
 * Follow the SDK layout: .highcode/.highcode.* run in RAM;
 * .highcode_fast/.highcode_lp execute in Flash as in the static example.
 * The relocation-bearing jump table keeps exported APIs alive under GC.
 */
ENTRY(_rom_entry)
ROM_BASE = DEFINED(ROM_BASE) ? ROM_BASE : 0x00020000;
USER_FLASH_END = 0x3C000;
ROM_JT_ENTRIES = DEFINED(ROM_JT_ENTRIES) ? ROM_JT_ENTRIES : 156;

MEMORY
{
    ROM (rx) : ORIGIN = ROM_BASE, LENGTH = USER_FLASH_END - ROM_BASE
    RAM (xrw) : ORIGIN = 0x20000000, LENGTH = 12K
}

SECTIONS
{
    .rom_header ROM_BASE :
    {
        KEEP(*(.rom_magic))
        KEEP(*(.rom_uuid))
    } >ROM
    ASSERT(SIZEOF(.rom_header) == 0x34, "BLE image header must be 0x34 bytes")

    .rom_jt (ROM_BASE + 0x34) :
    {
        KEEP(*(.rom_jt))
    } >ROM
    ASSERT(SIZEOF(.rom_jt) == ROM_JT_ENTRIES * 4, "BLE jump table size mismatch")

    .rom_version :
    {
        KEEP(*(.rom_version))
        . = ALIGN(4);
    } >ROM

    .text :
    {
        KEEP(*(.init))
        *(.text .text.*)
        *(.highcode_fast .highcode_fast.*)
        *(.highcode_lp .highcode_lp.*)
        *(.rodata .rodata.* .srodata .srodata.* .sdata2 .sdata2.*)
        . = ALIGN(4);
    } >ROM

    .highcode :
    {
        . = ALIGN(4);
        _rom_highcode_vma_start = .;
        *(.highcode .highcode.*)
        . = ALIGN(4);
        _rom_highcode_vma_end = .;
    } >RAM AT>ROM
    _rom_highcode_lma = LOADADDR(.highcode);

    .data :
    {
        . = ALIGN(4);
        _data_vma = .;
        PROVIDE(__global_pointer$ = . + 0x800);
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4);
        _edata = .;
    } >RAM AT>ROM
    _data_lma = LOADADDR(.data);

    .bss (NOLOAD) :
    {
        . = ALIGN(4);
        _sbss = .;
        *(.sbss .sbss.* .bss .bss.* COMMON)
        . = ALIGN(4);
        _ebss = .;
    } >RAM
    _end = _ebss;
    end = _ebss;

    _rom_start_addr = ROM_BASE;
    _rom_uuid_addr = ROM_BASE + 0x08;
    _rom_jt_start_addr = ROM_BASE + 0x34;
    _rom_end_addr = LOADADDR(.data) + SIZEOF(.data);
    _rom_size = _rom_end_addr - ROM_BASE;
    ROM_RAM_RESERVE_START = ORIGIN(RAM);
    ROM_RAM_RESERVE_END = _ebss;
    ASSERT(_rom_end_addr <= USER_FLASH_END, "BLE image exceeds user Flash")
    ASSERT(_ebss <= ORIGIN(RAM) + LENGTH(RAM), "BLE image exceeds SRAM")
    /DISCARD/ : { *(.comment .note.* .eh_frame* .riscv.attributes) }
}
