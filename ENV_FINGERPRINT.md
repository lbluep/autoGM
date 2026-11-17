# 环境指纹与属性伪装（仅记录，尚未执行）

> 状态：**未做任何修改**。本文档只是把调研结果和操作步骤存档，等需要时再按第 4 节执行。
> 记录日期：2026-11-14。相关文件：`INSTALL_MANIFEST.md`、`uninstall.sh`。

## 1. 结论

- `ro.product.model`、`ro.product.manufacturer` 等属性**可以改**，用 overlay 覆盖层实现，删除文件即可完全回滚。
- 但只改这两项没有意义：`ro.build.fingerprint`、ABI 列表、native bridge、内核、GL、传感器缺失仍然暴露环境，且“属性与硬件不符”本身就是一条新特征。
- 有若干项属于**硬指纹**，改不了或不能改（见第 5 节）。

## 2. 当前实测指纹（2026-11-14）

| 属性 | 当前值 |
|---|---|
| `ro.product.brand` | `waydroid` |
| `ro.product.manufacturer` | `Waydroid` |
| `ro.product.model` | `WayDroid x86_64 Device` |
| `ro.product.device` | `waydroid_x86_64` |
| `ro.product.name` | `lineage_waydroid_x86_64` |
| `ro.product.board` | 空 |
| `ro.hardware` | `unknown` |
| `ro.build.fingerprint` | `waydroid/lineage_waydroid_x86_64/waydroid_x86_64:13/TQ3A.230901.001/18:userdebug/test-keys` |
| `ro.build.tags` / `type` | `test-keys` / `userdebug` |
| `ro.build.user` / `host` | `jenkins` / `fl-a-w-1` |
| `ro.dalvik.vm.native.bridge` | `libndk_translation.so` |
| `ro.product.cpu.abilist64` | `x86_64,arm64-v8a` |
| `ro.hardware.egl` / `vulkan` | `mesa` / `intel_hasvk` |
| GL renderer | `Mesa Intel(R) HD Graphics 6000 (BDW GT3), OpenGL ES 3.2 Mesa 25.1.6` |
| `/proc/version` | `Linux 6.8.0-52-generic ... buildd@lcy02-amd64 ... Ubuntu` |
| 传感器 | `/sys/class/sensors` 不存在 |
| 电源/温度 | `/sys/class/power_supply/{BAT0,ADP1}`，`/sys/class/thermal/cooling_device*` |
| adb | `ro.adb.secure=0`，`service.adb.tcp.port=5555`，`adb_enabled=1` |

复核命令：

```bash
cd /home/fengg/playground/autogame/xingcheng-android
adb shell 'getprop | grep -E "ro\.(product|build|hardware|kernel)|dalvik\.vm\.native\.bridge"'
adb shell 'head -2 /proc/version'
adb shell 'ls /sys/class/sensors /sys/class/power_supply'
adb shell dumpsys SurfaceFlinger | grep -i "GLES"
```

## 3. 属性文件位置与优先级

| 路径 | 说明 | 建议 |
|---|---|---|
| `/var/lib/waydroid/rootfs/system/build.prop` | 镜像内原始文件（root-only，600，6241 字节） | 不要直接改；改坏无法回滚 |
| `/var/lib/waydroid/overlay/system/build.prop` | overlay 的首个 lowerdir，同名文件会遮蔽 rootfs 版本 | **推荐在这里新建覆盖文件** |
| `/var/lib/waydroid/overlay_rw/system/` | 运行时写入层，当前为空（`/` 以 ro 挂载） | 不适用 |
| `/var/lib/waydroid/waydroid.prop` | Waydroid 注入的功能属性（egl/vulkan/gralloc/native bridge 等） | 不要动，改了会破坏渲染或 ARM 翻译 |

挂载关系（实测）：

```
overlay on / type overlay (ro,
  lowerdir=/var/lib/waydroid/overlay:/var/lib/waydroid/rootfs,
  upperdir=/var/lib/waydroid/overlay_rw/system, ...)
```

`lowerdir` 中排在前面的是优先层，所以 `overlay/system/build.prop` 会覆盖镜像内的同名文件。

## 4. 以后要改时的步骤（需要 root / pkexec）

```bash
cd /home/fengg/playground/autogame/xingcheng-android

# 0. 先备份现有状态（只读，不改系统）
./stop.sh
adb shell 'getprop | grep -E "ro\.product|ro\.build|dalvik\.vm\.native\.bridge"' \
  > backup/props-before-$(date +%F).txt

# 1. 把原始 build.prop 复制到 overlay 层
pkexec cp /var/lib/waydroid/rootfs/system/build.prop \
            /var/lib/waydroid/overlay/system/build.prop
pkexec chmod 600 /var/lib/waydroid/overlay/system/build.prop

# 2. 成套改（示例：Pixel 7），用 sed 逐项替换
pkexec sed -i \
  -e 's|^ro.product.brand=.*|ro.product.brand=google|' \
  -e 's|^ro.product.manufacturer=.*|ro.product.manufacturer=Google|' \
  -e 's|^ro.product.model=.*|ro.product.model=Pixel 7|' \
  -e 's|^ro.product.device=.*|ro.product.device=panther|' \
  -e 's|^ro.product.name=.*|ro.product.name=panther|' \
  -e 's|^ro.product.board=.*|ro.product.board=panther|' \
  -e 's|^ro.build.fingerprint=.*|ro.build.fingerprint=google/panther/panther:13/TQ3A.230901.001/10750268:user/release-keys|' \
  -e 's|^ro.build.tags=.*|ro.build.tags=release-keys|' \
  -e 's|^ro.build.type=.*|ro.build.type=user|' \
  -e 's|^ro.build.user=.*|ro.build.user=android-build|' \
  -e 's|^ro.build.host=.*|ro.build.host=abfarm-release|' \
  /var/lib/waydroid/overlay/system/build.prop

# 3. 重启容器，让 property_service 重新读取
./start.sh

# 4. 验证
adb shell getprop ro.product.model
adb shell getprop ro.build.fingerprint
```

注意：

- `ro.*` 属性只在启动时读取，**必须重启容器**才生效，不能 `setprop` 热改。
- 如果某属性不在 `/system/build.prop` 里（可能在 `/vendor/build.prop`），sed 不会新增它，需要先确认它定义在哪个分区文件。
- `ro.build.type=user` 与 `ro.build.tags=release-keys` 只是字符串，不会让系统真的变成 user 镜像；某些依赖 `ro.debuggable` 的判断仍会看到原值。
- 改完不要保留 `waydroid` 字样残留在 `ro.product.*` 或 fingerprint 中，否则前后矛盾更显眼。

## 5. 改不了 / 不能改的硬指纹

| 项 | 原因 |
|---|---|
| `/proc/version` = Ubuntu 6.8.0-52-generic | Waydroid 共享宿主内核，容器内无法替换 |
| GL renderer = Mesa Intel HD 6000 | 真实 GPU 通过 mesa 暴露，无法伪装成 Adreno/Mali |
| `ro.hardware.egl=mesa`、`vulkan=intel_hasvk` | 改了就渲染不出画面 |
| 传感器缺失（`/sys/class/sensors`） | 宿主没有对应硬件；造假需要另写虚拟 sensor HAL |
| ACPI 电池/温度节点 | 来源于宿主 `/sys` |
| `ro.product.cpu.abilist*=x86_64,...` | 改了 ARM 翻译链会断，游戏无法启动 |
| `ro.dalvik.vm.native.bridge=libndk_translation.so` | 删掉后 ARM64 库无法加载 |
| Play Integrity / 硬件密钥认证 | 无 TEE 密钥，必然失败 |

## 6. 风险

- 只改 `model`/`manufacturer`：技术上零风险，但对风控几乎无效。
- 成套伪造：字段之间容易不一致（如 fingerprint 说 Pixel，GL 却是 Intel 核显），反而提高“伪装设备”嫌疑。
- TapSDK 等 SDK 的设备指纹可能包含上述无法伪造的项，服务端判定后本地无法补救。
- 如果游戏或 SDK 缓存了旧指纹，改属性后可能触发重新登录或风控复核。

## 7. 回滚

```bash
cd /home/fengg/playground/autogame/xingcheng-android
./stop.sh
pkexec rm -f /var/lib/waydroid/overlay/system/build.prop
./start.sh
adb shell getprop ro.product.model   # 应恢复为 WayDroid x86_64 Device
```

该回滚步骤也应加入 `uninstall.sh`（当前尚未加入，因为本次未做修改）。
