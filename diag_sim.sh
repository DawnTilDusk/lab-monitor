#!/bin/bash
# diag_sim.sh — 实验室监控仿真系统健康诊断脚本
# 用途：快速判断 sim_temp/light/image 是否真正运行成功

set -euo pipefail

LAB_DIR="/home/openEuler/lab_monitor"
LOG_DIR="/home/openEuler/opengauss_logs"
SIM_DIR="$LAB_DIR/simulators"
RELAY_HOST="${RELAY_HOST:-127.0.0.1}"
RELAY_PORT="${RELAY_PORT:-9999}"

log() { echo -e "\033[1;36m[DIAG]\033[0m $*"; }
ok()  { echo -e "\033[1;32m✅ $*\033[0m"; }
warn(){ echo -e "\033[1;33m⚠️  $*\033[0m"; }
err() { echo -e "\033[1;31m❌ $*\033[0m"; }

log "开始仿真系统诊断..."

# 1. 检查目录与脚本存在性
log "1. 检查仿真脚本是否存在"
SIM_SCRIPTS=("sim_temp.py" "sim_light.py" "sim_image.py")
all_exist=true
for f in "${SIM_SCRIPTS[@]}"; do
  if [ -f "$SIM_DIR/$f" ]; then
    ok "  $f"
  else
    err "  $f 未找到（路径：$SIM_DIR/$f）"
    all_exist=false
  fi
done
if [ "$all_exist" = false ]; then warn "→ 仿真脚本缺失，无法启动"; fi

# 2. 检查 PID 文件与进程存活
log "2. 检查仿真进程状态"
for name in temp light image; do
  pid_file="$LOG_DIR/sim_${name}.pid"
  if [ ! -f "$pid_file" ]; then
    err "  sim_$name: PID 文件缺失 ($pid_file)"
    continue
  fi
  pid=$(cat "$pid_file" 2>/dev/null || echo "")
  if [ -z "$pid" ] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
    err "  sim_$name: PID 文件内容无效 ('$pid')"
    continue
  fi
  if kill -0 "$pid" 2>/dev/null; then
    cmd=$(ps -p "$pid" -o args= 2>/dev/null | head -n1)
    ok "  sim_$name: PID=$pid 正在运行 → $cmd"
  else
    err "  sim_$name: PID=$pid 已退出"
    # 尝试从日志找最后错误
    if [ -f "$LOG_DIR/sim_${name}.log" ]; then
      last_err=$(tail -n 15 "$LOG_DIR/sim_${name}.log" | grep -iE "error|traceback|exception|no module|not found" | tail -n1)
      [ -n "$last_err" ] && echo "    └─ 最近错误: $last_err"
    fi
  fi
done

# 3. 检查仿真日志有无致命错误
log "3. 检查仿真日志关键错误"
for name in temp light image; do
  logf="$LOG_DIR/sim_${name}.log"
  if [ ! -f "$logf" ]; then
    warn "  $logf 不存在（可能从未启动成功）"
  else
    size=$(stat -c%s "$logf" 2>/dev/null)
    if [ "$size" -eq 0 ]; then
      warn "  $logf 为空（脚本可能立即退出）"
    else
      err_count=$(grep -ciE "traceback|error|exception|no module|importerror|modulenotfound|filenotfound|connection refused" "$logf" || true)
      if [ "$err_count" -gt 0 ]; then
        err "  $logf 含 $err_count 处错误（最近1条）:"
        tail -n 20 "$logf" | grep -iE "traceback|error|exception" -A5 -B2 | tail -n 5 | sed 's/^/    /'
      else
        ok "  $logf 无严重错误"
      fi
    fi
  fi
done

# 4. 检查 UDP 中继是否监听
log "4. 检查 UDP 中继 (relay) 是否监听 $RELAY_HOST:$RELAY_PORT"
if ss -uln | grep -q ":$RELAY_PORT "; then
  ok "  中继服务正在监听 UDP 端口 $RELAY_PORT"
else
  err "  未检测到 UDP 端口 $RELAY_PORT 监听！仿真脚本可能因连接失败退出"
  ps aux | grep -v grep | grep -E "udp_relay|relay" && echo "    → 发现 relay 进程（但可能未绑定正确端口）" || echo "    → 未发现 relay 进程"
fi

# 5. （可选）尝试发测试包
log "5. 尝试向 relay 发送测试 UDP 包（验证网络层）"
echo "TEST_PACKET" | timeout 2 nc -u -w1 "$RELAY_HOST" "$RELAY_PORT" >/dev/null 2>&1 && \
  ok "  UDP 测试包发送成功（网络层通）" || \
  warn "  UDP 测试包发送失败（可能防火墙/relay 未就绪）"

# 6. 检查 Python 环境关键依赖
log "6. 检查常用仿真依赖（粗略）"
deps=("numpy" "pillow" "opencv-python" "psutil")
for dep in "${deps[@]}"; do
  if python3 -c "import $dep" 2>/dev/null; then
    ok "  ✅ $dep"
  else
    warn "  ❓ $dep 未安装（部分仿真脚本可能需要）"
  fi
done

log "诊断完成。结论："
if pgrep -f "sim_.*\.py" >/dev/null; then
  ok "至少一个仿真进程正在运行"
else
  err "无活跃仿真进程 — 请检查日志或手动运行："
  echo "  python3 $SIM_DIR/sim_temp.py"
fi