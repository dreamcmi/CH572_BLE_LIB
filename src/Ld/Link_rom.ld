/* Application linker script for the fixed-address CH572 BLE image.
 * make_rom.ps1 generates the Flash partition and ROM RAM reservation below.
 * Startup copies application highcode/data; main_rom.c initializes ROM RAM.
 * Default: app [0,0x1F000), SNV [0x1F000,0x20000), ROM at 0x20000.
 */
ENTRY(_start)
INCLUDE rom_ram_info.ld

ASSERT(ROM_RAM_RESERVE_START == 0x20000000, "Invalid ROM RAM start")
ASSERT(ROM_RAM_RESERVE_END < 0x20003000, "ROM occupies all SRAM")

/* CH572 的 RAM 末尾（栈从这里向下生长） */
RAM_TOP = 0x20003000;

MEMORY
{
    /* app 主体：从 0x0 起的一整块连续空间，直到 SNV 区之前 */
    FLASH_APP (rx) : ORIGIN = 0x00000000, LENGTH = ROM_APP_FLASH_END

    /* 应用可用 RAM：从 ROM 变量区之后开始，到 RAM 末尾。
     * 这样应用的所有 RAM 段（.highcode/.data/.bss/栈）都排在 ROM 变量之后。 */
    RAM (xrw)      : ORIGIN = ROM_RAM_RESERVE_END, LENGTH = RAM_TOP - ROM_RAM_RESERVE_END
}

SECTIONS
{
    /* -------- 复位向量与启动代码：必须放 0x0 -------- */
    .init :
    {
        _sinit = .;
        . = ALIGN(4);
        KEEP(*(SORT_NONE(.init)))
        . = ALIGN(4);
        _einit = .;
    } >FLASH_APP AT>FLASH_APP

    /* -------- 应用主体：跟在启动代码后面，同一块连续空间 -------- */
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

    /* Immutable Flash vector table; strong application IRQ wrappers provide
     * the targets. Keeping vectors out of SRAM avoids 1024-byte RAM alignment
     * padding. Only the IRQ function bodies are copied into highcode RAM. */
    .vectors ALIGN(1024) :
    {
        . = ALIGN(4);
        KEEP(*(.vector))
        KEEP(*(SORT_NONE(.vector_handler)))
        . = ALIGN(4);
        PROVIDE(_vectors_end = .);
    } >FLASH_APP

    /* 应用自己的高速代码段：排在向量表之后 */
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

    /* -------- 明确标出各区域边界，便于排查问题 -------- */
    PROVIDE( _app_start            = 0x00000000 );   /* app 起点（复位向量） */
    PROVIDE( _app_end              = ROM_APP_FLASH_END - 1 );  /* app 可用空间结束 */
    PROVIDE( _snv_reserved_start   = SNV_ADDR );     /* SNV 区起点 */
    PROVIDE( _snv_reserved_end     = SNV_ADDR + SNV_SIZE - 1 );
    PROVIDE( _rom_reserved_start   = SNV_ADDR + SNV_SIZE ); /* ROM 区起点 */
    PROVIDE( _user_flash_end       = 0x0003BFFF );   /* 用户 Flash 末尾 */

    /* -------- 越界保护：app 不能长到 SNV 区里去 -------- */
    ASSERT(LOADADDR(.data) + SIZEOF(.data) <= ROM_APP_FLASH_END,
           "app 超出可用 Flash：请调小 ROM_APP_FLASH_END 之前的占用，或把 ROM 往后挪")
    ASSERT(_ebss + 1024 <= RAM_TOP, "CH572 requires at least 1024 bytes of stack")

    /DISCARD/ :
    {
        *(.comment)
        *(.note.*)
        *(.eh_frame*)
    }
}
