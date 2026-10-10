# =============================================================================
#  Makefile —— CH572 BLE 静态库工程（可选构建方式）
#
#  说明：
#      本工程的主构建方式是 scripts\build.ps1（Windows 一键脚本，无需 make）。
#      本 Makefile 供习惯 make 的同事、或在 Linux / MSYS2 环境下使用。
#
#  用法：
#      make              构建静态库 + 全部示例
#      make lib          只构建静态库
#      make example      只构建示例
#      make clean        清理
#      make tools        只重新生成符号表
#
#  指定工具链前缀（默认 riscv-none-embed-，即 MounRiver 自带）：
#      make CROSS=riscv32-esp-elf- EXTRA_DEFS="-DINT_SOFT -DUSE_STD_MCPY"
# =============================================================================

CROSS       ?= riscv-none-embed-
CC          := $(CROSS)gcc
AR          := $(CROSS)ar
OBJCOPY     := $(CROSS)objcopy
SIZE        := $(CROSS)size
NM          := $(CROSS)nm

ROOT        := .
BUILD       := $(ROOT)/build
OBJ         := $(BUILD)/obj
OUT         := $(BUILD)/out
LIBDIR      := $(ROOT)/lib

# ---------------------------------------------------------------------------
# 编译选项
#   -march=rv32imac_zicsr_zifencei : CH572 内核为青稞 RISC-V3C（RV32IMBC + 自定义扩展）
#   -DINT_SOFT / -DUSE_STD_MCPY    : 非 WCH 工具链需要（见 README 说明）
# ---------------------------------------------------------------------------
ARCH        := -march=rv32imac_zicsr_zifencei -mabi=ilp32 -mcmodel=medany -msmall-data-limit=8
OPT         ?= -Os
CFLAGS      := $(ARCH) $(OPT) -ffunction-sections -fdata-sections -fno-common -Wall -Wno-attributes -g
EXTRA_DEFS  ?=
CFLAGS      += $(EXTRA_DEFS)

INCLUDES    := -I$(ROOT)/include -I$(ROOT)/src/StdPeriphDriver -I$(ROOT)/src/RVMSIS

LDFLAGS     := $(ARCH) -nostartfiles -T$(ROOT)/src/Ld/Link.ld \
               -Wl,--gc-sections --specs=nano.specs --specs=nosys.specs

# ---------------------------------------------------------------------------
# 静态库
# ---------------------------------------------------------------------------
HAL_SRCS    := $(wildcard $(ROOT)/hal/*.c)
HAL_OBJS    := $(patsubst $(ROOT)/hal/%.c,$(OBJ)/lib/%.o,$(HAL_SRCS))

STACK_LIB   := $(LIBDIR)/libCH572BLE_PERI.a
OUT_LIB     := $(LIBDIR)/libCH572BLE.a
ISP_LIB     := $(LIBDIR)/libISP572.a

# ---------------------------------------------------------------------------
# 示例
# ---------------------------------------------------------------------------
EXAMPLE     ?= ble_uart
EX_DIR      := $(ROOT)/examples/$(EXAMPLE)
EX_SRCS     := $(wildcard $(EX_DIR)/src/*.c)
STD_SRCS    := $(addprefix $(ROOT)/src/StdPeriphDriver/,\
                 CH57x_clk.c CH57x_gpio.c CH57x_sys.c CH57x_flash.c CH57x_uart.c CH57x_pwr.c)
EX_OBJS     := $(OBJ)/$(EXAMPLE)/startup_CH572.o \
               $(patsubst $(EX_DIR)/src/%.c,$(OBJ)/$(EXAMPLE)/%.o,$(EX_SRCS)) \
               $(patsubst $(ROOT)/src/StdPeriphDriver/%.c,$(OBJ)/$(EXAMPLE)/%.o,$(STD_SRCS))

TARGET_ELF  := $(OUT)/$(EXAMPLE).elf
TARGET_HEX  := $(OUT)/$(EXAMPLE).hex

.PHONY: all lib example clean tools help

all: lib example

help:
	@echo "make all       - 构建静态库 + 示例"
	@echo "make lib       - 只构建静态库"
	@echo "make example   - 只构建示例 (EXAMPLE=ble_uart)"
	@echo "make clean     - 清理 build 目录"
	@echo "make tools     - 导出静态库符号表"

# --------------------------- 静态库 ---------------------------------------
lib: $(OUT_LIB)

$(OUT_LIB): $(HAL_OBJS) $(STACK_LIB)
	@mkdir -p $(OBJ)/lib/stack
	@cd $(OBJ)/lib/stack && $(AR) x $(abspath $(STACK_LIB))
	@rm -f $@
	@cd $(OBJ)/lib && $(AR) rcs $(abspath $@) \
	    $(notdir $(HAL_OBJS)) $(addprefix stack/,$(notdir $(wildcard $(OBJ)/lib/stack/*.o)))
	@echo "[OK] 生成 $@"

$(OBJ)/lib/%.o: $(ROOT)/hal/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INCLUDES) -c -o $@ $<

# --------------------------- 示例 -----------------------------------------
example: $(TARGET_HEX)

$(OBJ)/$(EXAMPLE)/startup_CH572.o: $(ROOT)/src/Startup/startup_CH572.S
	@mkdir -p $(dir $@)
	$(CC) $(ARCH) -x assembler-with-cpp -c -o $@ $<

$(OBJ)/$(EXAMPLE)/%.o: $(EX_DIR)/src/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INCLUDES) -I$(EX_DIR)/include -c -o $@ $<

$(OBJ)/$(EXAMPLE)/%.o: $(ROOT)/src/StdPeriphDriver/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $(INCLUDES) -I$(EX_DIR)/include -c -o $@ $<

$(TARGET_ELF): $(EX_OBJS) $(OUT_LIB)
	@mkdir -p $(OUT)
	$(CC) $(LDFLAGS) -Wl,-Map=$(OUT)/$(EXAMPLE).map -o $@ $(EX_OBJS) \
	      -L$(LIBDIR) -lCH572BLE -lISP572
	@echo "[OK] 生成 $@"

$(TARGET_HEX): $(TARGET_ELF)
	$(OBJCOPY) -O ihex $< $@
	@echo "[OK] 生成 $@"
	@$(SIZE) $(TARGET_ELF)

# --------------------------- 辅助 -----------------------------------------
tools: $(OUT_LIB)
	@$(NM) --defined-only --extern-only $(OUT_LIB) > $(BUILD)/libCH572BLE_symbols.txt
	@echo "[OK] 符号表: $(BUILD)/libCH572BLE_symbols.txt"

clean:
	rm -rf $(BUILD)
	@echo "[OK] 已清理 $(BUILD)"
