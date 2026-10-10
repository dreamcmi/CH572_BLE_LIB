/*******************************************************************************
 * 文件名称 : test_should_fail.c
 * 描    述 : 能力拦截宏的「反向」测试用例。
 *
 *            ★ 这个文件是故意写错的，它**必须编译失败**。★
 *
 *            它调用了 CH572 固件库不提供的主机角色 API。如果它能编译通过，
 *            说明 include/ch572_ble.h 里的能力拦截宏失效了，
 *            那样的话真实项目里误用主机 API 就会拖到链接期才暴露，
 *            面对 649 个 undefined reference 会非常难查。
 *
 *            验证方式：运行 scripts\run_tests.ps1
 ******************************************************************************/
#include "CONFIG.h"

void test_should_fail_central(void)
{
    /* ↓↓↓ 这一行必须触发编译期报错 ↓↓↓
     * 期望输出：
     *   error: call to '_ch572_ble_missing_api' declared with attribute error:
     *          CH572 BLE 固件库不提供该 API，请检查平台能力宏 CH572_BLE_HAS_xxx
     *   note: in expansion of macro 'GAPRole_CentralInit'
     */
    GAPRole_CentralInit();
}
