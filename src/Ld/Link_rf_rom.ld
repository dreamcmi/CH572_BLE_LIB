/* =============================================================================
 *  Link_rf_rom.ld —— 「使用纯 RF 固定地址镜像」的应用链接脚本
 *
 *  与 BLE 的 Link_rom.ld 同思路，但少了 SNV 那一段（纯 RF 没有绑定信息要存）。
 *
 *  两个关键约束（都由 make_rf_rom.ps1 自动生成的 rom_rf_ram_info.ld 提供）：
 *    1. 应用 RAM 从 RF 镜像变量区之后开始 —— 否则应用 .highcode 会覆盖镜像变量
 *    2. 应用 Flash 到镜像基址为止 —— 否则应用会踩到镜像代码
 * ========================================================================== */
ENTRY(_start)
INCLUDE rom_rf_ram_info.ld

ASSERT(RF_ROM_RAM_RESERVE_END < 0x20003000, "RF image occupies all SRAM")

RAM_TOP = 0x20003000;

MEMORY
{
    /* 应用主体：0x0 到镜像基址之前 */
    FLASH_APP (rx) : ORIGIN = 0x00000000, LENGTH = RF_ROM_APP_FLASH_END

    /* 应用 RAM：从镜像变量区之后开始，到 RAM 末尾。
     * 注意这里用的是 ROM 的 ORIGIN 覆盖，而不是 .rom_reserved (NOLOAD) 段 ——
     * BLE 那边试过后者，ld 不允许位置计数器在输出段之间往回走。 */
    RAM (xrw)      : ORIGIN = RF_ROM_RAM_RESERVE_END, LENGTH = RAM_TOP - RF_ROM_RAM_RESERVE_END
}

SECTIONS
{
    .init :
    {
        _sinit = .;
        . = ALIGN(4);
        KEEP(*(SORT_NONE(.init)))
        . = ALIGN(4);
        _einit = .;
    } >FLASH_APP AT>FLASH_APP

    .highcode_initlalign :
    {
        . = ALIGN(4);
        PROVIDE(_highcode_init_lma = .);
    } >FLASH_APP AT>FLASH_APP

    .highcodelalign :
    {
        . = ALIGN(4);
        PROVIDE(_highcode_lma = .);
    } >FLASH_APP AT>FLASH_APP

    /* 向量表放 Flash，省掉 SRAM 里 1024 字节对齐的浪费。
     * 应用必须提供强定义的 BB_IRQHandler / LLE_IRQHandler
     * （它们内部调跳转表里的 RF_BbIrqHandler / RF_LleIrqHandler）。 */
    .vectors ALIGN(1024) :
    {
        . = ALIGN(4);
        KEEP(*(.vector))
        KEEP(*(SORT_NONE(.vector_handler)))
        . = ALIGN(4);
        PROVIDE(_vectors_end = .);
    } >FLASH_APP

    .highcode :
    {
        . = ALIGN(4);
        _highcode_vma_start = .;
        *(.highcode);
        *(.highcode.*);
        *(.highcode_fast);
        *(.highcode_fast.*);
        *(.highcode_lp);
        *(.highcode_lp.*);
        . = ALIGN(4);
        PROVIDE(_highcode_vma_end = .);
    } >RAM AT>FLASH_APP
    _highcode_lma = LOADADDR(.highcode);

    .text :
    {
        . = ALIGN(4);
        KEEP(*(SORT_NONE(.handle_reset)))
        *(.text)
        *(.text.*)
        *(.rodata)
        *(.rodata*)
        *(.sdata2.*)
        *(.glue_7)
        *(.glue_7t)
        *(.gnu.linkonce.t.*)
        . = ALIGN(4);
    } >FLASH_APP AT>FLASH_APP

    .fini :
    {
        KEEP(*(SORT_NONE(.fini)))
        . = ALIGN(4);
    } >FLASH_APP AT>FLASH_APP

    PROVIDE( _etext = . );
    PROVIDE( _eitcm = . );

    .preinit_array :
    {
        PROVIDE_HIDDEN (__preinit_array_start = .);
        KEEP (*(.preinit_array))
        PROVIDE_HIDDEN (__preinit_array_end = .);
    } >FLASH_APP AT>FLASH_APP

    .init_array :
    {
        PROVIDE_HIDDEN (__init_array_start = .);
        KEEP (*(SORT_BY_INIT_PRIORITY(.init_array.*) SORT_BY_INIT_PRIORITY(.ctors.*)))
        KEEP (*(.init_array EXCLUDE_FILE (*crtbegin.o *crtbegin?.o *crtend.o *crtend?.o ) .ctors))
        PROVIDE_HIDDEN (__init_array_end = .);
    } >FLASH_APP AT>FLASH_APP

    .fini_array :
    {
        PROVIDE_HIDDEN (__fini_array_start = .);
        KEEP (*(SORT_BY_INIT_PRIORITY(.fini_array.*) SORT_BY_INIT_PRIORITY(.dtors.*)))
        KEEP (*(.fini_array EXCLUDE_FILE (*crtbegin.o *crtbegin?.o *crtend.o *crtend?.o ) .dtors))
        PROVIDE_HIDDEN (__fini_array_end = .);
    } >FLASH_APP AT>FLASH_APP

    .ctors :
    {
        KEEP (*crtbegin.o(.ctors))
        KEEP (*crtbegin?.o(.ctors))
        KEEP (*(EXCLUDE_FILE (*crtend.o *crtend?.o ) .ctors))
        KEEP (*(SORT(.ctors.*)))
        KEEP (*(.ctors))
    } >FLASH_APP AT>FLASH_APP

    .dtors :
    {
        KEEP (*crtbegin.o(.dtors))
        KEEP (*crtbegin?.o(.dtors))
        KEEP (*(EXCLUDE_FILE (*crtend.o *crtend?.o ) .dtors))
        KEEP (*(SORT(.dtors.*)))
        KEEP (*(.dtors))
    } >FLASH_APP AT>FLASH_APP

    .dlalign :
    {
        . = ALIGN(4);
        PROVIDE(_data_lma = .);
    } >FLASH_APP AT>FLASH_APP

    .data :
    {
        . = ALIGN(4);
        PROVIDE(_data_vma = .);
        *(.gnu.linkonce.r.*)
        *(.data .data.*)
        *(.gnu.linkonce.d.*)
        . = ALIGN(8);
        PROVIDE( __global_pointer$ = . + 0x800 );
        *(.sdata .sdata.*)
        *(.gnu.linkonce.s.*)
        . = ALIGN(8);
        *(.srodata.cst16)
        *(.srodata.cst8)
        *(.srodata.cst4)
        *(.srodata.cst2)
        *(.srodata .srodata.*)
        . = ALIGN(4);
        PROVIDE( _edata = .);
    } >RAM AT>FLASH_APP

    .bss :
    {
        . = ALIGN(4);
        PROVIDE( _sbss = .);
        *(.sbss*)
        *(.gnu.linkonce.sb.*)
        *(.bss*)
        *(.gnu.linkonce.b.*)
        *(COMMON*)
        . = ALIGN(4);
        PROVIDE( _ebss = .);
    } >RAM AT>FLASH_APP

    PROVIDE( _end = _ebss);
    PROVIDE( end = . );

    .stack ORIGIN(RAM)+LENGTH(RAM) :
    {
        . = ALIGN(4);
        PROVIDE(_eusrstack = . );
    } >RAM

    PROVIDE( _app_start          = 0x00000000 );
    PROVIDE( _rf_rom_base        = RF_ROM_APP_FLASH_END );
    PROVIDE( _rf_rom_ram_end     = RF_ROM_RAM_RESERVE_END );

    ASSERT(LOADADDR(.data) + SIZEOF(.data) <= RF_ROM_APP_FLASH_END,
           "app 超出可用 Flash：应用代码长到 RF 镜像那边去了")
    ASSERT(_ebss + 1024 <= RAM_TOP, "CH572 requires at least 1024 bytes of stack")

    /DISCARD/ :
    {
        *(.comment)
        *(.note.*)
        *(.eh_frame*)
    }
}
