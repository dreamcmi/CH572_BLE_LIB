/* =============================================================================
 *  rf_rom_link.ld —— 纯 RF 库的固定地址镜像链接脚本
 *
 *  与 BLE 的 rom_link.ld 是同一套思路，但有四处刻意的简化：
 *    1. **没有 UUID 表**：那是 BLE GATT 专用的 22 项标准 UUID，RF 用不上
 *    2. **头部布局重新定**：+0x00 魔数 / +0x08 版本串 / +0x10 跳转表
 *       （BLE 那边是为了贴合 WCH 官方 ABI 才用 +0x08/%22 项/+0x34）
 *    3. **保留版本串而不是自造**：BLE 那边踩过"版本串不匹配导致协议栈
 *       起不来"的坑，这里用 RF 库自己的 VER_RF_LIB 字符串
 *    4. **不做 ASSERT 版本校验**（没有 BLE 的 VER_FILE 那套机制），
 *       但保留尺寸断言防止镜像超界
 *
 *  跳转表项数：ROM_RF_JT_ENTRIES（由 make_rf_rom.ps1 用 --defsym 传入）
 * ========================================================================== */
ENTRY(_rom_rf_entry)

ROM_RF_BASE     = DEFINED(ROM_RF_BASE) ? ROM_RF_BASE : 0x00010000;
USER_FLASH_END  = 0x0003C000;      /* 用户 Flash 末尾（不含），上面 16 KB 是芯片固定 */
ROM_RF_JT_ENTRIES = DEFINED(ROM_RF_JT_ENTRIES) ? ROM_RF_JT_ENTRIES : 14;

MEMORY
{
    ROM (rx)  : ORIGIN = ROM_RF_BASE, LENGTH = USER_FLASH_END - ROM_RF_BASE
    RAM (xrw) : ORIGIN = 0x20000000,  LENGTH = 12K
}

SECTIONS
{
    /* ---------- 头部：魔数（8 B） ---------- */
    .rom_rf_header ROM_RF_BASE :
    {
        KEEP(*(.rom_rf_magic))
    } >ROM
    ASSERT(SIZEOF(.rom_rf_header) == 8, "RF image header must be 8 bytes")

    /* ---------- 版本串：从 ROM_RF_BASE + 0x08 起 ---------- */
    .rom_rf_version (ROM_RF_BASE + 0x08) :
    {
        KEEP(*(.rom_rf_version))
        . = ALIGN(4);
    } >ROM

    /* ---------- 跳转表：4 字节对齐，紧跟版本串 ---------- */
    .rom_rf_jt :
    {
        . = ALIGN(4);
        KEEP(*(.rom_rf_jt))
    } >ROM
    ASSERT(SIZEOF(.rom_rf_jt) == ROM_RF_JT_ENTRIES * 4, "RF jump table size mismatch")

    /* ---------- 代码 ----------
     * 注意分段策略：RF 库把大部分代码放进 .highcode（必须搬到 RAM 执行），
     * 只有一部分在 .text（Flash 直接执行）。
     * 这里与 BLE 的 rom_link.ld 保持一致：
     *   .text / .rodata        → 留在 Flash
     *   .highcode              → VMA 在 RAM，LMA 在 Flash（由应用搬运） */
    .text :
    {
        KEEP(*(.init))
        *(.text .text.*)
        *(.rodata .rodata.* .srodata .srodata.* .sdata2 .sdata2.*)
        . = ALIGN(4);
    } >ROM

    .highcode :
    {
        . = ALIGN(4);
        _rom_rf_highcode_vma_start = .;
        *(.highcode .highcode.*)
        *(.highcode_fast .highcode_fast.*)
        *(.highcode_lp .highcode_lp.*)
        . = ALIGN(4);
        _rom_rf_highcode_vma_end = .;
    } >RAM AT>ROM
    _rom_rf_highcode_lma = LOADADDR(.highcode);

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

    /* ---------- 导出给应用的符号 ---------- */
    _rom_rf_start_addr    = ROM_RF_BASE;
    _rom_rf_version_addr  = ROM_RF_BASE + 0x08;
    _rom_rf_jt_addr       = ADDR(.rom_rf_jt);
    _rom_rf_jt_end_addr   = ADDR(.rom_rf_jt) + SIZEOF(.rom_rf_jt);
    _rom_rf_end_addr      = LOADADDR(.data) + SIZEOF(.data);
    _rom_rf_size          = _rom_rf_end_addr - ROM_RF_BASE;

    ROM_RF_RAM_RESERVE_START = ORIGIN(RAM);
    ROM_RF_RAM_RESERVE_END   = _ebss;

    ASSERT(_rom_rf_end_addr <= USER_FLASH_END, "RF image exceeds user Flash")
    ASSERT(_ebss <= ORIGIN(RAM) + LENGTH(RAM), "RF image exceeds SRAM")

    /DISCARD/ : { *(.comment .note.* .eh_frame* .riscv.attributes) }
}
