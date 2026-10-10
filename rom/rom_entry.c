/*******************************************************************************
 * 文件名称 : rom_entry.c
 * 描    述 : ROM 镜像的入口与头部定义。
 *
 *            本文件负责构造一个"看起来像 WCH 官方 ROM 库"的镜像头部，
 *            布局与 CH59xBLE_ROM.h 里的约定完全一致：
 *
 *              ROM_BASE + 0x00  镜像标识区（8 字节：4 字节魔数 + 4 字节保留）
 *              ROM_BASE + 0x08  UUID 表（22 项，每项 2 字节，被 BLE_LIB_UUID(n) 索引）
 *              ROM_BASE + 0x34  函数跳转表（N 项，每项 4 字节，被 BLE_LIB_JT(n) 索引）
 *              ROM_BASE + 0x34 + N*4  版本字符串，之后是代码与只读数据
 *
 *            应用侧只包含生成出来的 CH572BLE_ROM.h，
 *            头文件把每个 API 定义为对跳转表里某个绝对地址的间接调用，
 *            因此应用**不需要链接任何 BLE 库**，固件体积能显著下降。
 ******************************************************************************/

#include <stdint.h>

/* ---------------------------------------------------------------------------
 * ROM 版本字符串
 *
 * ★ 这里必须写**协议栈的真实版本号**（与 CH572BLEPeri_LIB.h 里的 VER_FILE 一致），
 *   不能写成自定义名字。原因：应用启动时 CH57x_BLEInit() 会做一次版本校验 ——
 *
 *       if(tmos_memcmp(VER_LIB, VER_FILE, tmos_strlen(VER_FILE)) == FALSE) 停住
 *
 *   VER_LIB 指向本字符串，VER_FILE 来自头文件。两者前缀不一致就会报
 *   "head file error" 并停在 while(1) 里，BLE 完全起不来。
 *
 *   构建脚本（make_rom.ps1）会自动从 include/CH572BLE_ROM_types.h 里
 *   读出 VER_FILE，生成 rom_version.h 后包含进来，避免手工同步漏改。
 *   为什么不直接在命令行 -D 传字符串：shell 会吃掉引号，V1.3 里的点号
 *   会让编译器报 "'CH572_BLE_PERI_LIB_V1' undeclared"。
 * ------------------------------------------------------------------------- */
#include "rom_version.h"

/* ---------------------------------------------------------------------------
 * 镜像标识（占 ROM_BASE + 0x00 ~ 0x07，共 8 字节）
 *
 * 放在**单独的命名段** .rom_magic 里，而不是和 UUID 表挤在同一个段：
 * GNU ld 会按段名字母序排列同一输出段内的输入段，混在一起会让 UUID 表
 * 排到魔数前面，头部布局全错。
 *
 * 另外注意：这里用**一个 8 字节数组**而不是两个 uint32_t 变量。
 * 同一段内的变量顺序由编译器后端决定，-O 下不保证等于源码声明顺序，
 * 曾经因此出现"保留字排到了魔数前面"的错位；合成一个对象后地址完全确定，
 * 再由链接脚本用 ASSERT 断言校验（见 rom_link.ld）。
 * ------------------------------------------------------------------------- */
const uint8_t _rom_magic[8] __attribute__((section(".rom_magic"), used, aligned(8))) = {
    0x37, 0x35, 0x48, 0x43,     /* +0x00  '7','5','H','C'，小端读作 0x43483537 = "CH57" */
    0x00, 0x00, 0x00, 0x00,     /* +0x04  保留 */
};

/* ---------------------------------------------------------------------------
 * UUID 表：从 ROM_BASE + 0x08 开始，每项 2 字节，下标与 BLE_LIB_UUID(n) 对应。
 * 内容就是 GATT 标准 UUID 的小端表示，应用侧直接当 uint8_t[2] 使用。
 *
 * 表长固定 22 项：0x08 + 22*2 = 0x34，
 * 恰好让 .rom_header 结束在 ROM_BASE + 0x34，与官方约定（跳转表从 +0x34 起）严格对齐。
 * ------------------------------------------------------------------------- */
const uint8_t _rom_uuid_table[22][2] __attribute__((section(".rom_uuid"), used)) = {
    {0x00, 0x18},   /*  0  Generic Access             0x1800 */
    {0x01, 0x18},   /*  1  Generic Attribute          0x1801 */
    {0x00, 0x28},   /*  2  Primary Service            0x2800 */
    {0x01, 0x28},   /*  3  Secondary Service          0x2801 */
    {0x02, 0x28},   /*  4  Include                    0x2802 */
    {0x03, 0x28},   /*  5  Characteristic             0x2803 */
    {0x00, 0x29},   /*  6  Char Extended Properties    0x2900 */
    {0x01, 0x29},   /*  7  Char User Description       0x2901 */
    {0x02, 0x29},   /*  8  Client Char Configuration   0x2902 */
    {0x03, 0x29},   /*  9  Server Char Configuration   0x2903 */
    {0x04, 0x29},   /* 10  Char Presentation Format    0x2904 */
    {0x05, 0x29},   /* 11  Char Aggregate Format       0x2905 */
    {0x06, 0x29},   /* 12  Valid Range                 0x2906 */
    {0x07, 0x29},   /* 13  External Report Reference   0x2907 */
    {0x08, 0x29},   /* 14  Report Reference            0x2908 */
    {0x00, 0x2A},   /* 15  Device Name                 0x2A00 */
    {0x01, 0x2A},   /* 16  Appearance                  0x2A01 */
    {0x02, 0x2A},   /* 17  Peripheral Privacy Flag     0x2A02 */
    {0x03, 0x2A},   /* 18  Reconnection Address        0x2A03 */
    {0x04, 0x2A},   /* 19  Peri Preferred Conn Param   0x2A04 */
    {0x05, 0x2A},   /* 20  Service Changed             0x2A05 */
    {0x06, 0x2A},   /* 21  Central Address Resolution  0x2A06 */
};

/* ---------------------------------------------------------------------------
 * 编译期断言：UUID 表必须正好 44 字节，保证头部落在 +0x34
 * ------------------------------------------------------------------------- */
typedef char _rom_uuid_size_check[(sizeof(_rom_uuid_table) == 44) ? 1 : -1];

/* ---------------------------------------------------------------------------
 * ROM 版本字符串
 *   跳转表第 0 项（JT(0) / VER_LIB）指向它，应用可以读出来核对 ROM 版本。
 * ------------------------------------------------------------------------- */
const char _rom_version_str[] __attribute__((section(".rom_version"), used)) = ROM_VERSION_STR;

/*********************************************************************
 * @fn      _rom_entry
 *
 * @brief   ROM 镜像入口（仅用于满足链接器 ENTRY() 要求）。
 *
 * @note    ROM 不会自己上电启动 —— 上电启动由应用的 startup_CH572.S 完成，
 *          ROM 只是被应用按需调用。所有对外功能都从跳转表进入。
 */
__attribute__((section(".text.rom_entry"), used, noinline))
void _rom_entry(void)
{
    /* 空实现：ROM 被动被调用，不主动运行 */
}
