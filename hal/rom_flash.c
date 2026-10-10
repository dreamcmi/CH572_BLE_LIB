/* Reuse the fixed image's ISP code instead of allocating a second RAM copy. */
#include "CONFIG.h"

#ifdef CH572BLE_ROM
uint32_t FLASH_EEPROM_CMD(uint8_t cmd, uint32_t addr, void *buffer, uint32_t length)
{
    return CH572BLE_ROM_FlashCommand(cmd, addr, buffer, length);
}
#endif
