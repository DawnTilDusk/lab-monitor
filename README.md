# 昆仑哨兵·实验室多模态监控系统

## 项目概述

昆仑哨兵是一款专为实验室环境设计的多模态监控系统，部署于Orange Pi Kunpeng Pro开发板。系统集成了温度监测、图像采集、气泡检测和数据可视化功能，为实验室提供智能化的环境监测解决方案。

## 系统特性

### 🔧 硬件集成
- **温度采集**：DS18B20数字温度传感器，GPIO2_15接口
- **图像采集**：标准UVC USB摄像头，支持实时图像捕获
- **数据存储**：openGauss 5.0.0数据库，本地化数据持久化

### 🌐 Web界面
- **实时监控**：温度/光敏/图像，SSE事件推送低时延刷新
- **数据可视化**：ECharts温度趋势，增量绘图与异常点标注
- **开源社区**：模型库与脚本展示，支持假数据渲染与示例说明
- **数据库页**：表单化查询，支持 `sensor_data` 与 `model_outputs`
- **响应式设计**：适配多种显示设备

### 🔧 技术栈
- **后端**：Flask 2.x + Python 3.9
- **数据库**：openGauss 5.0.0（PostgreSQL兼容）
- **前端**：HTML5/CSS3/JavaScript + ECharts
- **图像处理**：OpenCV-Python 4.x
- **采集与中转**：C采集器 + UDP中转
- **模型**：Python周期模型 + 模型管理器

### PIN
Pin1 有 3.3V 电源功能
Pin2 有 5V 电源功能
Pin3 有 I²C7_SDA / GPIO2_12 / GPIO#76 功能
Pin4 有 5V 电源功能
Pin5 有 I²C7_SCL / GPIO2_11 / GPIO#75 功能
Pin6 有 GND 功能
Pin7 有 UART7_TX / GPIO7_02 / GPIO#226 功能
Pin8 有 UART0_TX / GPIO0_14 / GPIO#14 功能
Pin9 有 GND 功能
Pin10 有 UART0_RX / GPIO0_15 / GPIO#15 功能
Pin11 有 CAN_RX3 / URXD2 / GPIO2_18 / GPIO#82 功能
Pin13 有 GPIO1_06 / GPIO#38 功能
Pin15 有 GPIO2_15 / GPIO#79 功能
Pin16 有 GPIO2_16 / GPIO#80 功能
Pin17 有 3.3V 电源功能
Pin18 有 GPIO0_25 / GPIO#25 功能
Pin19 有 SPI0_MOSI / GPIO2_27 / GPIO#91 功能
Pin20 有 GND 功能
Pin21 有 SPI0_MISO / GPIO2_28 / GPIO#92 功能
Pin22 有 GPIO0_02 / GPIO#2 功能
Pin23 有 SPI0_SCLK / GPIO2_25 / GPIO#89 功能
Pin24 有 SPI0_CS / GPIO2_26 / GPIO#90 功能
Pin25 有 GND 功能
Pin26 有 GPIO2_19 / GPIO#83 功能
Pin27 有 I²C6_SDA 功能（无 GPIO 复用，电压 1.8V）
Pin28 有 I²C6_SCL 功能（无 GPIO 复用，电压 1.8V）
Pin29 有 URXD7 / GPIO7_07 / GPIO#231 功能
Pin30 有 GND 功能
Pin31 有 GPIO2_20 / GPIO#84 功能
Pin32 有 PWM3 / GPIO1_01 / GPIO#33 功能
Pin33 有 GPIO4_00 / GPIO#128 功能
Pin34 有 GND 功能
Pin35 有 GPIO7_04 / GPIO#228 功能
Pin36 有 UTXD2 / CAN_TX3 / GPIO2_17 / GPIO#81 功能
Pin37 有 GPIO0_03 / GPIO#3 功能
Pin38 有 GPIO7_06 / GPIO#230 功能
Pin39 有 GND 功能
Pin40 有 GPIO7_05 / GPIO#229 功能
 📌 此外，Pin8/Pin10 为调试串口 UART0（TX/RX），已与 Micro USB 调试口复用，不建议另作他用。

⚠️ 补充说明：

Pin11/Pin36 默认为 UART2（URXD2/UTXD2），但也可通过设备树切换为 CAN3（CAN_RX3/CAN_TX3）；二者不可同时启用。
Pin27/Pin28 仅有 I²C 功能，不可作 GPIO 使用，且为 1.8V 电平，与其余 3.3V GPIO 不同。
所有标 GPIO 的引脚默认为 3.3V 电平。
全板共 26 个 GPIO 引脚（含复用为 UART/SPI/I²C/PWM 的 pin）。

## 系统架构

```
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│   浏览器     │◄────►│   Flask后端  │◄────►│  openGauss   │
│  仪表盘/DB页 │  REST │  REST + SSE  │  SQL  │  数据存储    │
└──────────────┘       └──────────────┘       └──────────────┘
         ▲                        ▲
         │                        │
         │               ┌─────────────────────┐
         │               │  模型管理器（周期） │
         │               │  读取stdout→UDP     │
         │               └─────────────────────┘
         │                        ▲
         ▼                        │
┌───────────────────────┐   ┌───────────────────────┐
│  采集器（C，温/光/图）│→UDP→│  中转（UDP→入库→通知） │
└───────────────────────┘   └───────────────────────┘
             ▲                           │
             └──────────SSE事件──────────┘
```

## 文件结构

```
/home/openEuler/lab_monitor/
├── app.py                   # Flask主应用与API/SSE
├── start_lab_monitor.sh     # 一键启动脚本（DB/后端/采集/中转/模型）
├── db_init.sql              # 数据库初始化脚本（幂等）
├── relay/
│   └── udp_relay.py         # UDP中转：入库、图像保存、后端通知
├── sensor_collectors/       # C采集器（温度/光敏/图像）及Makefile
├── models/                  # 周期模型与管理器
│   ├── model_01.py ...      # 模型脚本（stdout输出JSON行）
│   ├── model_manager.py     # 模型管理器（启动/状态/UDP发送）
│   └── config.json          # 自启动与模型元信息
├── static/
│   ├── css/style.css        # 样式
│   ├── js/main.js           # 前端逻辑（仪表盘/模型/社区）
│   └── images/              # 图像存储目录
├── templates/
│   ├── index.html           # 监控首页
│   ├── open_source.html     # 开源社区（模型库）
│   └── db.html              # 数据库页（表单化查询）
└── README.md                # 项目说明文档
```

## 快速开始

### 1. 环境准备

```bash
# 操作系统：openEuler 22.03 LTS SP4（鲲鹏）
# 必备组件：openGauss、毕昇、make、Python3.9
```

### 2. 一键启动

```bash
cd /home/openEuler/lab_monitor
bash start_lab_monitor.sh
```

说明：脚本将幂等配置并启动 openGauss、Flask 后端、UDP中转、C采集器与模型管理器，并进行端口/防火墙/探针检查。

### 3. 访问系统

- 监控首页：`http://<设备IP>:5000/`
- 开源社区：`http://<设备IP>:5000/open-source`
- 数据库页：`http://<设备IP>:5000/db`

## 采集器与中转

- 采集器：原生 C 进程分别采集温度、光敏与图像，按统一 JSON 通过 UDP 上报
- 中转：接收 UDP，补全/保存图像、写入 `sensor_data`，并通知后端触发 SSE 更新
- 示例与探针：一键脚本内置示例任务与健康探针，便于联调与演示

## API接口

- `GET /api/latest`：最新状态与数据
- `GET /api/history?hours=<n>`：历史数据
- `GET /api/events`：SSE实时事件流
- `POST /api/capture`：触发采集（兼容摄像）
- `POST /api/ingest`：外部数据接入
- `POST /api/relay_notify`：中转通知后端刷新
- `POST /api/model_output`：模型输出直传入库
- `GET /api/models`、`POST /api/models/command`、`POST /api/models/notify`、`GET /api/models/download/<name>`：模型管理
- `GET /api/db/tables`、`POST /api/db/query`、`POST /api/db/clear`：数据库页表单化查询与清理

## 硬件配置

### DS18B20温度传感器
- **接口**：GPIO2_15 (40-pin第15号引脚)
- **上拉电阻**：4.7kΩ到3.3V
- **数据读取**：`/sys/bus/w1/devices/28-*/w1_slave`

### UVC摄像头
- **设备路径**：`/dev/video0`
- **分辨率**：640x480（可配置）
- **格式**：BGR/GRAY（按采集器配置）

### 模型（Python周期）
- **运行方式**：stdout 输出 JSON 行，管理器读取后通过 UDP 上报
- **配置**：`models/config.json` 定义自启动与模型元信息
- **入库**：写入 `model_outputs(name, output, created_at)`

## 部署指南（推荐）

1. **准备环境**：openEuler + openGauss + gcc/make + Python3.9
2. **克隆与放置**：确保工程位于 `/home/openEuler/lab_monitor`
3. **运行脚本**：`bash start_lab_monitor.sh`
4. **访问页面**：浏览器打开 `http://<设备IP>:5000/`
5. **日志与探针**：脚本输出含健康检查与各服务日志路径

## 使用说明

### 仪表盘
- **实时监控**：SSE事件推送，温度/光敏/图像状态秒级刷新
- **趋势图**：ECharts增量绘图，支持24小时历史查看

### 开源社区
- **模型卡片**：展示与下载脚本，支持假数据渲染示例
- **脚本管理**：结合后端接口进行启停与自启设置

### 数据库页
- **表单化查询**：支持 `sensor_data` 的温度/光敏/图像表单与 `model_outputs` 的模型表单
- **分页与清理**：结果分页展示；支持清空 `sensor_data`

## 故障排除

### 常见问题

1. **DS18B20传感器离线**
   ```bash
   # 检查设备
   ls /sys/bus/w1/devices/28-*
   
   # 检查GPIO
   gpio readall
   ```

2. **摄像头无法访问**
   ```bash
   # 检查设备
   ls -la /dev/video0
   
   # 测试摄像头
   fswebcam test.jpg
   ```

3. **数据库连接失败**
   ```bash
   # 检查服务状态
   systemctl status gaussdb
   
   # 测试连接
   gsql -h localhost -p 7654 -U labuser -d lab_monitor
   ```

4. **NPU模型加载失败**
   ```bash
   # 检查模型文件
   ls -la /home/openEuler/lab_monitor/model/bubble_detector.om
   
   # 当前版本使用随机数模拟，实际部署需要真实模型
   ```

### 错误处理

系统采用健壮的错误处理机制：
- 传感器离线返回：`{"error": "xxx offline"}`
- 数据库错误返回：`{"error": "database error"}`
- 摄像头错误返回：`{"error": "camera offline"}`

## 开发指南

### 扩展传感器
在`app.py`中添加新的传感器读取函数：
```python
def read_new_sensor():
    try:
        # 传感器读取逻辑
        return {"sensor_value": value}
    except Exception as e:
        return {"error": "sensor offline"}
```

### 添加新模型
1. 在 `models/` 目录编写 `model_xx.py`，周期执行并 `print(json.dumps(...))`
2. 在 `models/config.json` 中注册模型，可选设置开机自启
3. 通过后端模型管理页面或接口启动/停止，输出将自动入库与推送

### 前端定制
- 修改`static/css/style.css`调整样式
- 修改`templates/index.html`调整页面布局
- 修改`static/js/main.js`添加交互功能

## 开源协议

本项目采用 Apache-2.0 许可证：允许商业使用、修改与分发，需保留版权与许可声明。

## 社区贡献

欢迎提交您的实验室专用模型！

- **GitHub**：github.com/kunlunsentinel/lab-models
- **邮件**：contact@kunlunsentinel.com
- **论坛**：community.kunlunsentinel.com

## 技术支持

如遇到问题，请通过以下方式获取支持：

1. **查看日志**：`journalctl -u kunlun-sentinel -f`
2. **检查配置**：确认所有路径和权限设置
3. **社区求助**：在GitHub提交Issue
4. **文档查阅**：参考项目Wiki文档

---

**昆仑哨兵** - 让实验室监控更智能！

*Built with ❤️ for the openEuler community*
