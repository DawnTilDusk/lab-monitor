#!/usr/bin/env bash

# 昆仑哨兵·实验室多模态监控系统 一键启动脚本（Orange Pi Kunpeng Pro）
# 使用 openEuler 用户运行，包含：环境变量、openGauss启动、数据库初始化、Flask服务后台启动
# 目标端口：数据库 15400，Web 5000

set -euo pipefail

# 禁止以 root/sudo 运行（openGauss 不允许 root 运行）
if [ "$(id -u)" -eq 0 ]; then
  echo "[ERROR] 检测到以 root 运行。请不要使用 sudo，改为以普通用户（openEuler）运行本脚本。"
  echo "[HINT] 如之前用 sudo 创建了数据/日志目录，请修复所有权："
  echo "       sudo chown -R openEuler:openEuler /home/openEuler/opengauss_data /home/openEuler/opengauss_logs"
  exit 1
fi

############################
# 环境与路径
############################
export GAUSSHOME=/usr/local/opengauss
export PATH="$GAUSSHOME/bin:$PATH"
export LD_LIBRARY_PATH="$GAUSSHOME/lib:${LD_LIBRARY_PATH:-}"

LAB_DIR="/home/openEuler/lab_monitor"
DATA_DIR="/home/openEuler/opengauss_data"
LOG_DIR="/home/openEuler/opengauss_logs"
export GAUSSLOG="$LOG_DIR"
DB_PORT=7654
FLASK_PORT=5000

mkdir -p "$DATA_DIR" "$LOG_DIR"

# 数据库连接环境变量默认值（可被外部传入覆盖）
DEFAULT_DB_PASSWORD="LabUser@12345"
export DB_PASSWORD="${DB_PASSWORD:-$DEFAULT_DB_PASSWORD}"

echo "[INIT] GAUSSHOME=$GAUSSHOME"
echo "[INIT] LAB_DIR=$LAB_DIR"
echo "[INIT] DATA_DIR=$DATA_DIR"
echo "[INIT] LOG_DIR=$LOG_DIR"
echo "[INIT] GAUSSLOG=$GAUSSLOG"

############################
# 统一进程来源：脚本加锁与来源检查
############################
# 支持 --force 参数跳过并发锁
SKIP_LOCK=0
if [ "${1:-}" = "--force" ]; then
  SKIP_LOCK=1
fi

# 使用锁文件避免并发启动同一脚本（并处理陈旧锁）
LOCK_FILE="$LOG_DIR/ks_flask.lock"
exec 9>"$LOCK_FILE"

# 提供 --stop 子命令：一键停止 Flask、旧脚本实例，并清理锁文件
if [ "${1:-}" = "--stop" ]; then
  echo "[APP] 执行停止与清理..."
  # 停止 Flask 各种可能的运行方式
  pkill -f "$LAB_DIR/app.py" 2>/dev/null || true
  pkill -f "flask run" 2>/dev/null || true
  pkill -f "gunicorn.*app:app" 2>/dev/null || true
  pkill -f "$LAB_DIR/relay/udp_relay.py" 2>/dev/null || true
  pkill -f "$LAB_DIR/simulators/sim_temp.py" 2>/dev/null || true
  pkill -f "$LAB_DIR/simulators/sim_light.py" 2>/dev/null || true
  pkill -f "$LAB_DIR/simulators/sim_image.py" 2>/dev/null || true
  # 停止其他脚本实例
  pgrep -f "start_lab_monitor.sh" | grep -v "$$" | xargs -r kill -TERM 2>/dev/null || true
  sleep 1
  pgrep -f "start_lab_monitor.sh" | grep -v "$$" | xargs -r kill -KILL 2>/dev/null || true
  # 尝试终止持有锁的进程
  if command -v fuser >/dev/null 2>&1; then
    fuser -k "$LOCK_FILE" 2>/dev/null || true
  elif command -v lsof >/dev/null 2>&1; then
    for pid in $(lsof -t "$LOCK_FILE" 2>/dev/null || true); do
      kill -TERM "$pid" 2>/dev/null || true
      sleep 1
      kill -KILL "$pid" 2>/dev/null || true
    done
  fi
  # 清理锁与 PID 文件
  rm -f "$LOCK_FILE" "$LOG_DIR/ks_script.pid" "$LOG_DIR/flask_app.pid" "$LOG_DIR/udp_relay.pid" "$LOG_DIR/sim_temp.pid" "$LOG_DIR/sim_light.pid" "$LOG_DIR/sim_image.pid"
  echo "[APP] 停止与清理完成"
  exit 0
fi
acquire_lock_with_cleanup() {
  # 已持有 FD9，尝试获取锁；失败则清理旧实例与陈旧锁后重试
  if flock -n 9; then
    return 0
  fi

  # 读取之前记录的脚本 PID，并构建需要终止的旧实例列表
  PID_FILE="$LOG_DIR/ks_script.pid"
  OLD_PID=""
  if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" | tr -d ' ')
  fi

  KILL_LIST=""
  if [ -n "$OLD_PID" ] && [ "$OLD_PID" != "$$" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    KILL_LIST="$KILL_LIST $OLD_PID"
  fi
  for pid in $(pgrep -f "start_lab_monitor.sh" || true); do
    if [ "$pid" != "$$" ]; then
      case " $KILL_LIST " in *" $pid "*) ;; *) KILL_LIST="$KILL_LIST $pid";; esac
    fi
  done
  KILL_LIST=$(echo "$KILL_LIST" | xargs 2>/dev/null || true)

  if [ -n "$KILL_LIST" ]; then
    echo "[APP] 检测到另一个启动脚本实例，正在停止: $KILL_LIST"
    kill -TERM $KILL_LIST 2>/dev/null || true
    # 等待最多 3 秒以释放锁
    for i in 1 2 3; do
      sleep 1
      flock -n 9 && break || true
    done
    # 仍未释放则强制结束
    if ! flock -n 9; then
      STILL_ALIVE=""
      for pid in $KILL_LIST; do
        kill -0 "$pid" 2>/dev/null && STILL_ALIVE="$STILL_ALIVE $pid"
      done
      STILL_ALIVE=$(echo "$STILL_ALIVE" | xargs 2>/dev/null || true)
      [ -n "$STILL_ALIVE" ] && kill -KILL $STILL_ALIVE 2>/dev/null || true
    fi
    echo "[APP] 旧实例已停止，继续启动。"
  else
    echo "[APP] 发现锁但未检测到脚本进程，认为是陈旧锁，尝试清理后继续..."
  fi

  # 最多等待 5 秒重试拿锁
  if flock -w 5 9; then
    return 0
  fi

  # 仍失败：尝试定位并终止持锁进程（fuser/lsof）
  HOLDER_PIDS_RAW=""
  if command -v fuser >/dev/null 2>&1; then
    HOLDER_PIDS_RAW=$(fuser "$LOCK_FILE" 2>/dev/null | tr ' ' '\n' | xargs 2>/dev/null || true)
  elif command -v lsof >/dev/null 2>&1; then
    HOLDER_PIDS_RAW=$(lsof -t "$LOCK_FILE" 2>/dev/null | xargs 2>/dev/null || true)
  fi
  HOLDER_PIDS=""
  for pid in $HOLDER_PIDS_RAW; do
    # 过滤当前脚本与父进程，避免误杀自身导致终止
    if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
      case " $HOLDER_PIDS " in *" $pid "*) ;; *) HOLDER_PIDS="$HOLDER_PIDS $pid";; esac
    fi
  done
  HOLDER_PIDS=$(echo "$HOLDER_PIDS" | xargs 2>/dev/null || true)
  if [ -n "$HOLDER_PIDS" ]; then
    echo "[APP] 检测到持锁进程，正在终止: $HOLDER_PIDS"
    kill -TERM $HOLDER_PIDS 2>/dev/null || true
    sleep 1
    for pid in $HOLDER_PIDS; do
      kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
    done
    # 尝试重新获取锁
    if flock -w 3 9; then
      return 0
    fi
  fi

  # 最后手段：如确实没有脚本进程持有锁，清理锁文件并重建 FD 再试
  if ! pgrep -af "start_lab_monitor.sh" >/dev/null 2>&1; then
    rm -f "$LOCK_FILE" 2>/dev/null || true
    exec 9>"$LOCK_FILE"
    if flock -w 3 9; then
      return 0
    fi
  fi
  return 1
}

if [ "$SKIP_LOCK" -ne 1 ]; then
  if ! acquire_lock_with_cleanup; then
    echo "[APP] 仍无法获取锁，请稍后重试或使用 --force"
    exit 1
  fi
else
  echo "[APP] 跳过并发锁检查（--force）"
fi

# 成功获取锁后记录当前脚本 PID（辅助排查）
echo "$$" > "$LOG_DIR/ks_script.pid" || true

# 如果检测到 systemd 服务在运行，则自动停止并禁用，统一进程来源
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-active --quiet kunlun-sentinel; then
    echo "[APP] 检测到 systemd 服务 'kunlun-sentinel' 正在运行，尝试自动停止以统一进程来源..."
    if sudo -n systemctl stop kunlun-sentinel 2>/dev/null; then
      echo "[APP] 已停止 systemd 服务 'kunlun-sentinel'"
      sudo -n systemctl disable kunlun-sentinel 2>/dev/null || true
    else
      echo "[WARN] 无法自动停止 'kunlun-sentinel'（需要 sudo 权限）。请手动执行："
      echo "       sudo systemctl stop kunlun-sentinel && sudo systemctl disable kunlun-sentinel"
      exit 1
    fi
  fi
fi

# 目录所有者检查提示（若非当前用户）
DATA_OWNER=$(ls -ld "$DATA_DIR" | awk '{print $3}')
LOG_OWNER=$(ls -ld "$LOG_DIR" | awk '{print $3}')
if [ "$DATA_OWNER" != "$USER" ] || [ "$LOG_OWNER" != "$USER" ]; then
  echo "[WARN] 数据/日志目录所有者不是当前用户($USER)：DATA_OWNER=$DATA_OWNER, LOG_OWNER=$LOG_OWNER"
  echo "[HINT] 如需修复：sudo chown -R $USER:$USER $DATA_DIR $LOG_DIR"
fi

############################
# 前置校验
############################
if ! command -v gs_ctl >/dev/null 2>&1; then
  echo "[ERROR] gs_ctl 未找到，请确认 openGauss 已安装于 $GAUSSHOME 并设置 PATH"
  exit 1
fi
if ! command -v gsql >/dev/null 2>&1; then
  echo "[ERROR] gsql 未找到，请确认 openGauss 客户端已安装于 $GAUSSHOME 并设置 PATH"
  exit 1
fi

############################
# 初始化数据目录（首次）
############################
if [ ! -f "$DATA_DIR/postgresql.conf" ]; then
  echo "[DB] 数据目录未初始化，执行 gs_initdb..."
  gs_initdb -D "$DATA_DIR" --nodename=single_node
fi

# 配置端口与监听地址（幂等）
conf_file="$DATA_DIR/postgresql.conf"
if grep -qE "^port[[:space:]]*=[[:space:]]*$DB_PORT" "$conf_file"; then
  echo "[DB] 端口已设置为 $DB_PORT"
else
  if grep -qE "^[#[:space:]]*port[[:space:]]*=" "$conf_file"; then
    sed -i "s/^[#[:space:]]*port[[:space:]]*=.*/port = $DB_PORT/" "$conf_file"
  else
    echo "port = $DB_PORT" >> "$conf_file"
  fi
  echo "[DB] 已写入端口配置: $DB_PORT"
fi

if grep -qE "^listen_addresses[[:space:]]*=[[:space:]]*'\*'" "$conf_file"; then
  echo "[DB] listen_addresses 已为 '*'"
else
  if grep -qE "^[#[:space:]]*listen_addresses[[:space:]]*=" "$conf_file"; then
    sed -i "s/^[#[:space:]]*listen_addresses[[:space:]]*=.*/listen_addresses = '*'/" "$conf_file" || true
    # 若替换失败（不同 sed 行为），则追加
  fi
  if ! grep -qE "^listen_addresses[[:space:]]*=[[:space:]]*'\*'" "$conf_file"; then
    echo "listen_addresses = '*'" >> "$conf_file"
  fi
  echo "[DB] 已写入 listen_addresses='*'"
fi

# 强制密码加密类型为 md5，避免后端客户端不支持 openGauss 的 SASL/sha256 机制
if grep -qE "^password_encryption_type" "$conf_file"; then
  sed -i "s/^password_encryption_type.*/password_encryption_type = 0/" "$conf_file" || true
else
  echo "password_encryption_type = 0" >> "$conf_file"
fi
echo "[DB] 已设置 password_encryption_type = 0 (md5)"

# 配置 pg_hba 允许本机 TCP 连接（幂等）
hba_file="$DATA_DIR/pg_hba.conf"
if [ -f "$hba_file" ]; then
  # 允许本地 UNIX socket 使用 peer 免密认证
  if ! grep -qE "^local[[:space:]]+all[[:space:]]+all[[:space:]]+peer" "$hba_file"; then
    echo "local    all    all    peer" >> "$hba_file"
    echo "[DB] 已添加 pg_hba (local peer)"
  fi
  # 若存在针对 127.0.0.1 的 sha256 规则，替换为 md5（避免 psycopg2 与 openGauss 的 SASL 机制不兼容）
  if grep -qE "^host[[:space:]]+all[[:space:]]+all[[:space:]]+127\.0\.0\.1/32[[:space:]]+sha256" "$hba_file"; then
    sed -i "s/^host[[:space:]]\+all[[:space:]]\+all[[:space:]]\+127\\.0\\.0\\.1\/32[[:space:]]\+sha256/host    all    all    127.0.0.1\/32    md5/" "$hba_file"
    echo "[DB] 已将 pg_hba (IPv4 localhost sha256) 替换为 md5"
  fi
  if ! grep -q "127.0.0.1/32" "$hba_file"; then
    echo "host    all    all    127.0.0.1/32    md5" >> "$hba_file"
    echo "[DB] 已添加 pg_hba (IPv4 localhost)"
  fi
  if ! grep -q "::1/128" "$hba_file"; then
    echo "host    all    all    ::1/128    md5" >> "$hba_file"
    echo "[DB] 已添加 pg_hba (IPv6 localhost)"
  fi
  # 为确保 md5 规则优先生效，将 IPv4 md5 规则提升到文件顶部（若尚未在顶部）
  if ! head -n 5 "$hba_file" | grep -qE "^host[[:space:]]+all[[:space:]]+all[[:space:]]+127\.0\.0\.1/32[[:space:]]+md5"; then
    sed -i '1i host    all    all    127.0.0.1/32    md5' "$hba_file"
    echo "[DB] 已将 pg_hba (IPv4 md5) 规则置顶"
  fi
  # 兜底修正：删除任何 labuser@127.0.0.1 的 password 规则，改为置顶 md5 规则
  sed -i -E '/^host[[:space:]]+all[[:space:]]+labuser[[:space:]]+127\.0\.0\.1\/32[[:space:]]+password$/d' "$hba_file" || true
  if ! head -n 3 "$hba_file" | grep -qE "^host[[:space:]]+all[[:space:]]+labuser[[:space:]]+127\.0\.0\.1/32[[:space:]]+md5"; then
    sed -i '1i host    all    labuser    127.0.0.1/32    md5' "$hba_file"
    echo "[DB] 已置顶规则：host all labuser 127.0.0.1/32 md5"
  fi
  # 统一将所有 host/hostssl 的 sha256 规则替换为 md5，避免客户端落入 SASL 认证
  if grep -qE "^(host|hostssl)[[:space:]].*sha256" "$hba_file"; then
    sed -i -E "s/^(host|hostssl)([[:space:]].*)sha256/\1\2md5/" "$hba_file"
    echo "[DB] 已批量将 pg_hba 中的 sha256 规则替换为 md5"
  fi
  # 打印前 15 行确认规则优先级
  echo "[DB] pg_hba.conf 前 15 行："; head -n 15 "$hba_file" | sed 's/^/[DB]     /'
fi

############################
# 启动 openGauss（若未运行）
############################
if gs_ctl status -D "$DATA_DIR" >/dev/null 2>&1; then
  echo "[DB] openGauss 已在运行，应用最新配置并重启..."
  gs_ctl restart -D "$DATA_DIR" -Z single_node -l "$LOG_DIR/gs_ctl.log"
else
  echo "[DB] 启动 openGauss..."
  gs_ctl start -D "$DATA_DIR" -Z single_node -l "$LOG_DIR/gs_ctl.log"
fi
 # 若启动失败，立即回显 gs_ctl.log 末尾帮助定位
 if ! gs_ctl status -D "$DATA_DIR" >/dev/null 2>&1; then
   echo "[ERROR] openGauss 启动失败，展示 $LOG_DIR/gs_ctl.log 末尾："
   tail -n 120 "$LOG_DIR/gs_ctl.log" | sed 's/^/[DB]     /' || true
 fi

# 等待数据库就绪
echo "[DB] 等待数据库就绪（本地 socket peer 认证）..."
READY=0
for i in {1..30}; do
  if gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "SELECT 1;" >/dev/null 2>&1; then
    echo "[DB] 数据库连接成功"
    READY=1
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "[ERROR] 数据库未就绪，请检查 $LOG_DIR/gs_ctl.log 和 $conf_file"
  echo "[HINT] 端口占用/防火墙/监听地址可能导致连接失败"
fi
SHOW_PET=$(gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "SHOW password_encryption_type;" 2>/dev/null | awk 'NR==3{print $1}')
echo "[DB] 验证 password_encryption_type: ${SHOW_PET:-unknown}"

############################
# 输出过滤函数（忽略可预期/幂等提示）
############################
filter_ignorable() {
  # 过滤可忽略的提示/错误，保留关键输出
  grep -vE "NOTICE:|already exists|New password should not equal|creating new table with existing name|Non-SSL connection"
}

############################
# 设置本地管理员 openEuler 密码（若未设置）
############################
echo "[DB] 设置 openEuler 密码（若未设置），忽略已设置错误..."
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "ALTER ROLE \"openEuler\" PASSWORD 'OpenEuler@123';" 2>&1 | filter_ignorable || true

############################
# 创建业务用户与库（若不存在）
############################
echo "[DB] 创建用户 labuser（若不存在），忽略已存在错误..."
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "CREATE ROLE \"labuser\" PASSWORD '${DB_PASSWORD}';" 2>&1 | filter_ignorable || true
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "CREATE USER labuser WITH PASSWORD '${DB_PASSWORD}';" 2>&1 | filter_ignorable || true
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "ALTER ROLE labuser WITH LOGIN;" 2>&1 | filter_ignorable || true
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "ALTER ROLE labuser PASSWORD '${DB_PASSWORD}';" 2>&1 | filter_ignorable || true
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "ALTER ROLE labuser ACCOUNT UNLOCK;" 2>&1 | filter_ignorable || true


echo "[DB] 创建数据库 lab_monitor（若不存在），忽略已存在错误..."
gsql -d postgres -U openEuler -p "$DB_PORT" -r -c "CREATE DATABASE lab_monitor OWNER labuser;" 2>&1 | filter_ignorable || true

# 修复 openGauss/PG 环境下 labuser 无法在 public 模式建表的问题
echo "[DB] 为 labuser 授予 public 模式 USAGE/CREATE 权限..."
gsql -d lab_monitor -U openEuler -p "$DB_PORT" -r -c "GRANT USAGE ON SCHEMA public TO labuser;" 2>&1 | filter_ignorable || true
gsql -d lab_monitor -U openEuler -p "$DB_PORT" -r -c "GRANT CREATE ON SCHEMA public TO labuser;" 2>&1 | filter_ignorable || true

# 可选：导入项目初始化脚本（不阻塞失败）
if [ -f "$LAB_DIR/db_init.sql" ]; then
  echo "[DB] 导入 db_init.sql（若对象已存在将忽略错误）..."
  gsql -p "$DB_PORT" -U openEuler -d lab_monitor -f "$LAB_DIR/db_init.sql" 2>&1 | filter_ignorable || true
fi

############################
# 启动 Flask 应用（后台）
############################
if pgrep -f "$LAB_DIR/app.py" >/dev/null 2>&1; then
  echo "[APP] 检测到 Flask 已在运行，准备重启..."
  pkill -f "$LAB_DIR/app.py" || true
  sleep 1
fi
# 额外清理可能的其他运行方式（flask run / gunicorn）
pkill -f "flask run" || true
pkill -f "gunicorn.*app:app" || true
echo "[APP] 启动 Flask 应用（端口 $FLASK_PORT）..."
# 导出数据库连接环境变量（优先 TCP 连接，避免 UNIX socket 路径不匹配）
export DB_HOST="${DB_HOST:-127.0.0.1}"
export DB_PORT="$DB_PORT"
export DB_NAME="${DB_NAME:-lab_monitor}"
export DB_USER="${DB_USER:-labuser}"
export FLASK_PORT="$FLASK_PORT"
export PATH="/usr/local/bin:/usr/bin:/usr/sbin:$PATH"
if [ -z "${DB_PASSWORD:-}" ]; then
  export DB_PASSWORD="LabUser@12345"
fi

export LIGHT_GPIO_ACTIVE_HIGH="${LIGHT_GPIO_ACTIVE_HIGH:-1}"
export LIGHT_GPIO_GROUP="${LIGHT_GPIO_GROUP:-2}"
export LIGHT_GPIO_PIN="${LIGHT_GPIO_PIN:-20}"
if [ -n "${LIGHT_GPIO_GROUP:-}" ] && [ -n "${LIGHT_GPIO_PIN:-}" ]; then
  LG_CALC=$((LIGHT_GPIO_GROUP*32 + LIGHT_GPIO_PIN))
  export LIGHT_GPIO="$LG_CALC"
fi
if [ -z "${LIGHT_GPIO:-}" ]; then
  export LIGHT_GPIO="17"
fi

if [ -n "${LIGHT_GPIO:-}" ]; then
  if [ ! -e "/sys/class/gpio/gpio${LIGHT_GPIO}/value" ]; then
    if command -v sudo >/dev/null 2>&1; then
      sudo -n sh -c "echo ${LIGHT_GPIO} > /sys/class/gpio/export" >/dev/null 2>&1 || true
      sudo -n sh -c "echo in > /sys/class/gpio/gpio${LIGHT_GPIO}/direction" >/dev/null 2>&1 || true
    else
      echo "${LIGHT_GPIO}" > /sys/class/gpio/export 2>/dev/null || true
      echo "in" > /sys/class/gpio/gpio${LIGHT_GPIO}/direction 2>/dev/null || true
    fi
    if [ ! -e "/sys/class/gpio/gpio${LIGHT_GPIO}/value" ]; then
      echo "[WARN] GPIO ${LIGHT_GPIO} 未就绪，可能需要 root 权限导出。请执行: sudo sh -c 'echo ${LIGHT_GPIO} > /sys/class/gpio/export; echo in > /sys/class/gpio/gpio${LIGHT_GPIO}/direction'" >> "$LOG_DIR/sensor_light.log"
    fi
  fi
fi

# 启动并记录 PID
nohup python3 -u "$LAB_DIR/app.py" >> "$LOG_DIR/flask_app.log" 2>&1 &
FLASK_PID=$!
echo "[APP] Flask 已启动，PID=${FLASK_PID}，日志：$LOG_DIR/flask_app.log"
echo "${FLASK_PID}" > "$LOG_DIR/flask_app.pid" || true

# 健康探针：主动请求一次 /api/latest，触发后端记录数据库探测日志
if command -v curl >/dev/null 2>&1; then
  sleep 1
  curl -s "http://127.0.0.1:$FLASK_PORT/api/latest" >/dev/null || true
fi

# 再次确认仅存在一个 Flask 进程（信息性提示）
sleep 1
FLASK_PIDS=$(pgrep -f "$LAB_DIR/app.py" 2>/dev/null || true)
FLASK_COUNT=$(echo "$FLASK_PIDS" | wc -l | tr -d ' ')
[ -z "$FLASK_COUNT" ] && FLASK_COUNT=0
if [ "$FLASK_COUNT" -gt 1 ]; then
  echo "[WARN] 检测到多个 Flask 进程实例（$FLASK_COUNT）。已统一由脚本启动，建议手工终止其他来源。"
fi

 

echo "=============================================="
echo "✅ 启动完成"
DEFAULT_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
if [ -z "$DEFAULT_IP" ]; then
  DEFAULT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
fi
ALL_IPS=$(hostname -I 2>/dev/null)
if [ -n "$DEFAULT_IP" ]; then
  echo "- Web:   http://$DEFAULT_IP:$FLASK_PORT/ (所有地址: $ALL_IPS)"
else
  echo "- Web:   http://$(hostname -I | awk '{print $1}'):$FLASK_PORT/ (所有地址: $ALL_IPS)"
fi
echo "- DB:    port=$DB_PORT  data_dir=$DATA_DIR"
echo "- Logs:  $LOG_DIR"
echo "=============================================="

REL_DIR="$LAB_DIR/relay"
if [ -f "$REL_DIR/udp_relay.py" ]; then
  pkill -f "$REL_DIR/udp_relay.py" 2>/dev/null || true
  LAB_DIR="$LAB_DIR" IMAGE_TTL_SEC="${IMAGE_TTL_SEC:-600}" IDLE_IMAGE_SEC="${IDLE_IMAGE_SEC:-10}" nohup python3 -u "$REL_DIR/udp_relay.py" >> "$LOG_DIR/udp_relay.log" 2>&1 &
  echo $! > "$LOG_DIR/udp_relay.pid" || true
  echo "[RELAY] UDP中转已启动 PID $(cat "$LOG_DIR/udp_relay.pid" 2>/dev/null || echo unknown)" >> "$LOG_DIR/udp_relay.log"
fi

SC_DIR="$LAB_DIR/sensor_collectors"
sleep 1
CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/video1}"

if [ ! -e "$CAMERA_DEVICE" ]; then
  if [ -e "/dev/video0" ]; then
    CAMERA_DEVICE="/dev/video0"
  fi
fi

LIGHT_GPIO="${LIGHT_GPIO:-80}"
LIGHT_GPIO_ACTIVE_HIGH="${LIGHT_GPIO_ACTIVE_HIGH:-1}"
if [ -d "$SC_DIR" ]; then
  if [ -x "/opt/bisheng/bin/gcc" ]; then
    make -C "$SC_DIR" CC="/opt/bisheng/bin/gcc" CFLAGS="${CFLAGS:- -O3 -mcpu=native -ffp-contract=fast}" >> "$LOG_DIR/sensor_collectors_build.log" 2>&1 || true
  else
    make -C "$SC_DIR" >> "$LOG_DIR/sensor_collectors_build.log" 2>&1 || true
  fi
  if [ -x "$SC_DIR/sensor_temp_collector" ]; then
    pkill -f "$SC_DIR/sensor_temp_collector" 2>/dev/null || true
    LAB_DIR="$LAB_DIR" RELAY_HOST="${RELAY_HOST:-127.0.0.1}" RELAY_PORT="${RELAY_PORT:-9999}" nohup "$SC_DIR/sensor_temp_collector" >> "$LOG_DIR/sensor_temp.log" 2>&1 &
    echo $! > "$LOG_DIR/sensor_temp.pid" || true
    echo "[SENSOR] 温度采集已启动 PID $(cat "$LOG_DIR/sensor_temp.pid" 2>/dev/null || echo unknown)" >> "$LOG_DIR/sensor_temp.log"
  fi
  if [ -x "$SC_DIR/sensor_light_collector" ]; then
    pkill -f "$SC_DIR/sensor_light_collector" 2>/dev/null || true
    if [ -n "${LIGHT_GPIO_GROUP:-}" ] && [ -n "${LIGHT_GPIO_PIN:-}" ]; then
      GOP="gpio_operate"
      if [ -x "/usr/bin/gpio_operate" ]; then GOP="/usr/bin/gpio_operate"; elif [ -x "/usr/sbin/gpio_operate" ]; then GOP="/usr/sbin/gpio_operate"; elif [ -x "/bin/gpio_operate" ]; then GOP="/bin/gpio_operate"; fi
      if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
        sudo -n "$GOP" set_direction "$LIGHT_GPIO_GROUP" "$LIGHT_GPIO_PIN" 0 >> "$LOG_DIR/sensor_light.log" 2>&1 || echo "[WARN] gpio_operate set_direction 失败" >> "$LOG_DIR/sensor_light.log"
      else
        "$GOP" set_direction "$LIGHT_GPIO_GROUP" "$LIGHT_GPIO_PIN" 0 >> "$LOG_DIR/sensor_light.log" 2>&1 || true
      fi
    fi
    if command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
      LAB_DIR="$LAB_DIR" RELAY_HOST="${RELAY_HOST:-127.0.0.1}" RELAY_PORT="${RELAY_PORT:-9999}" LIGHT_GPIO="${LIGHT_GPIO:-}" LIGHT_GPIO_ACTIVE_HIGH="${LIGHT_GPIO_ACTIVE_HIGH:-1}" LIGHT_SYSFS="${LIGHT_SYSFS:-}" LIGHT_ADC_CHANNEL="${LIGHT_ADC_CHANNEL:-}" LIGHT_GPIO_GROUP="${LIGHT_GPIO_GROUP:-}" LIGHT_GPIO_PIN="${LIGHT_GPIO_PIN:-}" nohup sudo -E "$SC_DIR/sensor_light_collector" >> "$LOG_DIR/sensor_light.log" 2>&1 &
    else
      LAB_DIR="$LAB_DIR" RELAY_HOST="${RELAY_HOST:-127.0.0.1}" RELAY_PORT="${RELAY_PORT:-9999}" LIGHT_GPIO="${LIGHT_GPIO:-}" LIGHT_GPIO_ACTIVE_HIGH="${LIGHT_GPIO_ACTIVE_HIGH:-1}" LIGHT_SYSFS="${LIGHT_SYSFS:-}" LIGHT_ADC_CHANNEL="${LIGHT_ADC_CHANNEL:-}" LIGHT_GPIO_GROUP="${LIGHT_GPIO_GROUP:-}" LIGHT_GPIO_PIN="${LIGHT_GPIO_PIN:-}" nohup "$SC_DIR/sensor_light_collector" >> "$LOG_DIR/sensor_light.log" 2>&1 &
      echo "[WARN] 光敏采集未以 root 运行，gpio_operate 可能不可用" >> "$LOG_DIR/sensor_light.log"
    fi
    echo $! > "$LOG_DIR/sensor_light.pid" || true
    echo "[SENSOR] 光敏采集已启动 PID $(cat "$LOG_DIR/sensor_light.pid" 2>/dev/null || echo unknown)" >> "$LOG_DIR/sensor_light.log"
  fi
  if [ -x "$SC_DIR/sensor_image_collector" ]; then
    pkill -f "$SC_DIR/sensor_image_collector" 2>/dev/null || true
    echo "[SENSOR] 摄像头设备 ${CAMERA_DEVICE}" >> "$LOG_DIR/sensor_image.log"
    LAB_DIR="$LAB_DIR" RELAY_HOST="${RELAY_HOST:-127.0.0.1}" RELAY_PORT="${RELAY_PORT:-9999}" CAMERA_DEVICE="$CAMERA_DEVICE" nohup "$SC_DIR/sensor_image_collector" >> "$LOG_DIR/sensor_image.log" 2>&1 &
    echo $! > "$LOG_DIR/sensor_image.pid" || true
    echo "[SENSOR] 图像采集已启动 PID $(cat "$LOG_DIR/sensor_image.pid" 2>/dev/null || echo unknown)" >> "$LOG_DIR/sensor_image.log"
  fi
else
  echo "[SENSOR] 目录不存在：$SC_DIR" >> "$LOG_DIR/flask_app.log"
fi

if command -v ss >/dev/null 2>&1; then
  ss -lntp | awk -v p="$FLASK_PORT" '$4 ~ ":"p {print "[NET]", $0}' || true
elif command -v netstat >/dev/null 2>&1; then
  netstat -plnt 2>/dev/null | awk -v p="$FLASK_PORT" '$4 ~ ":"p {print "[NET]", $0}' || true
fi
if command -v curl >/dev/null 2>&1; then
  curl -s -o /dev/null -w "[PROBE] http://${DEFAULT_IP:-127.0.0.1}:$FLASK_PORT/ -> %{http_code}\n" "http://${DEFAULT_IP:-127.0.0.1}:$FLASK_PORT/" || true
  for ip in $(echo "$ALL_IPS" | tr ' ' '\n' | grep -v ':'); do
    curl -s -o /dev/null -w "[PROBE] http://${ip}:$FLASK_PORT/ -> %{http_code}\n" "http://${ip}:$FLASK_PORT/" || echo "[PROBE] http://${ip}:$FLASK_PORT/ -> 000"
  done
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  if ! firewall-cmd --list-ports | grep -q "${FLASK_PORT}/tcp"; then
    if sudo -n firewall-cmd --permanent --add-port="${FLASK_PORT}/tcp" >/dev/null 2>&1; then
      sudo -n firewall-cmd --reload >/dev/null 2>&1 || true
      echo "[NET] open ${FLASK_PORT}/tcp"
    else
      echo "[WARN] sudo firewall-cmd --permanent --add-port=${FLASK_PORT}/tcp && sudo firewall-cmd --reload"
    fi
  fi
fi

# iptables fallback (FirewallD not running)
if command -v iptables >/dev/null 2>&1; then
  if ! sudo -n iptables -C INPUT -p tcp --dport "$FLASK_PORT" -j ACCEPT >/dev/null 2>&1; then
    if sudo -n iptables -I INPUT -p tcp --dport "$FLASK_PORT" -j ACCEPT >/dev/null 2>&1; then
      echo "[NET] iptables opened ${FLASK_PORT}/tcp"
    else
      echo "[WARN] sudo iptables -I INPUT -p tcp --dport $FLASK_PORT -j ACCEPT"
    fi
  fi
fi

EX_LOG="$LOG_DIR/examples.log"
run_example_temp() {
  python3 - <<'PY' 2>&1
import os, json, urllib.request
p=int(os.environ.get('FLASK_PORT','5000'))
u=f"http://127.0.0.1:{p}/api/history?hours=6"
try:
    d=json.loads(urllib.request.urlopen(u,timeout=5).read().decode('utf-8'))
    vals=[x.get('value') for x in d.get('temperature_data',[]) if isinstance(x,dict) and 'value' in x]
    avg=round(sum(vals)/len(vals),2) if vals else None
    print(f"[EXAMPLE] 温度 n={len(vals)} avg={avg}")
except Exception as e:
    print("[EXAMPLE] 温度脚本失败:", e)
PY
}

run_example_cam() {
  python3 - <<'PY' 2>&1
import os, json, urllib.request
p=int(os.environ.get('FLASK_PORT','5000'))
url=f"http://127.0.0.1:{p}/api/capture"
req=urllib.request.Request(url, data=b"{}", headers={'Content-Type':'application/json'})
try:
    d=json.loads(urllib.request.urlopen(req,timeout=10).read().decode('utf-8'))
    path=d.get('image_path','')
    if path:
        img=urllib.request.urlopen(f"http://127.0.0.1:{p}{path}",timeout=10).read()
        open('/tmp/cam_sample.jpg','wb').write(img)
        print(f"[EXAMPLE] 摄像图像 {path} -> /tmp/cam_sample.jpg")
    else:
        print("[EXAMPLE] 摄像脚本无图像")
except Exception as e:
    print("[EXAMPLE] 摄像脚本失败:", e)
PY
}

run_example_light() {
  python3 - <<'PY' 2>&1
import os, json, urllib.request
p=int(os.environ.get('FLASK_PORT','5000'))
u=f"http://127.0.0.1:{p}/api/latest"
try:
    d=json.loads(urllib.request.urlopen(u,timeout=5).read().decode('utf-8'))
    lv=d.get('light', None)
    print(f"[EXAMPLE] 光敏值 {lv if lv is not None else 'N/A'}")
except Exception as e:
    print("[EXAMPLE] 光敏脚本失败:", e)
PY
}

run_example_ingest() {
  python3 - <<'PY' 2>&1
import os, json, random, urllib.request, zlib, time
p=int(os.environ.get('FLASK_PORT','5000'))
w=h=8
pixels=[]
buf=bytearray()
for y in range(h):
    row=[]
    for x in range(w):
        r=random.randint(0,255); g=random.randint(0,255); b=random.randint(0,255)
        row.append({'r':r,'g':g,'b':b})
        buf.extend([r,g,b])
    pixels.append(row)
checksum=f"{zlib.crc32(buf)&0xFFFFFFFF:08x}"
payload={
  'device_id':'sim-001',
  'timestamp_ms':int(time.time()*1000),
  'temperature_c': round(random.uniform(22.0,28.0),1),
  'light': random.randint(300,800),
  'frame': {'width':w,'height':h,'pixels':pixels},
  'checksum_frame': checksum
}
req=urllib.request.Request(f"http://127.0.0.1:{p}/api/ingest", data=json.dumps(payload).encode('utf-8'), headers={'Content-Type':'application/json'})
try:
    resp=urllib.request.urlopen(req,timeout=10).read().decode('utf-8')
    print(f"[EXAMPLE] 模拟发送完成 {resp}")
except Exception as e:
    print("[EXAMPLE] 模拟脚本失败:", e)
PY
}

mkdir -p "$LOG_DIR" || true
nohup python3 -u "$LAB_DIR/scripts/script_monitor.py" >> "$LOG_DIR/script_monitor.log" 2>&1 &
echo $! > "$LOG_DIR/script_monitor.pid"
echo "[MON] 脚本状态监控已启动 PID $(cat "$LOG_DIR/script_monitor.pid")" >> "$LOG_DIR/script_monitor.log"
if [ -f "$LAB_DIR/scripts/light_sender" ]; then
  chmod u+x "$LAB_DIR/scripts/light_sender" 2>/dev/null || true
fi

# 模型管理器
if [ -f "$LAB_DIR/models/model_manager.py" ]; then
  pkill -f "$LAB_DIR/models/model_manager.py" 2>/dev/null || true
  LAB_DIR="$LAB_DIR" RELAY_HOST="${RELAY_HOST:-127.0.0.1}" RELAY_PORT="${RELAY_PORT:-9999}" FLASK_PORT="${FLASK_PORT:-5000}" nohup python3 -u "$LAB_DIR/models/model_manager.py" >> "$LOG_DIR/model_manager.log" 2>&1 &
  echo $! > "$LOG_DIR/model_manager.pid" || true
  echo "[MON] 模型管理已启动 PID $(cat "$LOG_DIR/model_manager.pid" 2>/dev/null || echo unknown)" >> "$LOG_DIR/model_manager.log"
fi
