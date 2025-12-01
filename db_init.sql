-- 昆仑哨兵·实验室多模态监控系统
-- 数据库初始化脚本
-- 适用于openGauss 5.0.0

-- 创建数据库
CREATE DATABASE lab_monitor
    WITH 
    OWNER = labuser
    ENCODING = 'UTF8'
    LC_COLLATE = 'en_US.UTF-8'
    LC_CTYPE = 'en_US.UTF-8'
    TABLESPACE = pg_default
    CONNECTION LIMIT = -1;

-- 授予权限
GRANT CONNECT, TEMPORARY ON DATABASE lab_monitor TO PUBLIC;
GRANT ALL ON DATABASE lab_monitor TO labuser;

-- 使用数据库
\c lab_monitor;

-- 创建传感器数据表
CREATE TABLE sensor_data (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    temperature REAL NOT NULL,
    image_path TEXT NOT NULL,
    light INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
-- 兼容已有库：确保缺失列按需补齐（openGauss不支持 ADD COLUMN IF NOT EXISTS）
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'sensor_data' AND column_name = 'light'
    ) THEN
        EXECUTE 'ALTER TABLE sensor_data ADD COLUMN light INT';
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'sensor_data' AND column_name = 'bubble_count'
    ) THEN
        EXECUTE 'ALTER TABLE sensor_data ADD COLUMN bubble_count INT DEFAULT 0';
    END IF;
    EXECUTE 'ALTER TABLE sensor_data ALTER COLUMN bubble_count SET DEFAULT 0';
END$$;

-- 添加表注释
COMMENT ON TABLE sensor_data IS '传感器数据采集表';
COMMENT ON COLUMN sensor_data.id IS '数据记录ID';
COMMENT ON COLUMN sensor_data.timestamp IS '数据采集时间戳';
COMMENT ON COLUMN sensor_data.temperature IS '温度值(摄氏度)';
COMMENT ON COLUMN sensor_data.image_path IS '图像文件路径';
COMMENT ON COLUMN sensor_data.light IS '光敏值(照度lx或原始值)';
COMMENT ON COLUMN sensor_data.created_at IS '记录创建时间';

-- 创建索引
CREATE INDEX idx_sensor_timestamp ON sensor_data(timestamp DESC);
CREATE INDEX idx_sensor_temperature ON sensor_data(temperature);
CREATE INDEX idx_sensor_created_at ON sensor_data(created_at DESC);

-- 创建系统状态表
CREATE TABLE system_status (
    id SERIAL PRIMARY KEY,
    component VARCHAR(50) NOT NULL,
    status VARCHAR(20) NOT NULL,
    last_check TIMESTAMPTZ DEFAULT NOW(),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 添加表注释
COMMENT ON TABLE system_status IS '系统组件状态表';
COMMENT ON COLUMN system_status.component IS '组件名称(ds18b20/camera/db)';
COMMENT ON COLUMN system_status.status IS '组件状态(online/offline/error)';
COMMENT ON COLUMN system_status.last_check IS '最后检查时间';
COMMENT ON COLUMN system_status.error_message IS '错误信息';

-- 创建索引
CREATE INDEX idx_system_status_component ON system_status(component);
CREATE INDEX idx_system_status_status ON system_status(status);
CREATE INDEX idx_system_status_last_check ON system_status(last_check DESC);

-- 创建模型信息表（用于扩展功能）

-- 创建数据采集历史统计视图（幂等）
DROP VIEW IF EXISTS sensor_statistics;
CREATE VIEW sensor_statistics AS
SELECT 
    timestamp::date AS date,
    COUNT(*) AS total_records,
    CAST(AVG(temperature) AS numeric(10,2)) AS avg_temperature,
    CAST(MIN(temperature) AS numeric(10,2)) AS min_temperature,
    CAST(MAX(temperature) AS numeric(10,2)) AS max_temperature,
    MIN(timestamp) as first_record,
    MAX(timestamp) as last_record
FROM sensor_data 
GROUP BY timestamp::date
ORDER BY date DESC;

-- 创建实时数据视图（幂等）
DROP VIEW IF EXISTS latest_sensor_data;
CREATE VIEW latest_sensor_data AS
SELECT 
    sd.*,
    CASE 
        WHEN temperature < 20 THEN '低温'
        WHEN temperature > 30 THEN '高温'
        ELSE '正常'
    END as temp_status
FROM sensor_data sd
WHERE sd.timestamp = (SELECT MAX(timestamp) FROM sensor_data);

-- 授予权限
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO labuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO labuser;
GRANT SELECT ON sensor_statistics TO labuser;
GRANT SELECT ON latest_sensor_data TO labuser;

-- 基础数据插入（测试用）
INSERT INTO sensor_data (temperature, image_path) VALUES
(25.2, '/static/images/test1.jpg'),
(25.5, '/static/images/test2.jpg'),
(24.8, '/static/images/test3.jpg'),
(25.1, '/static/images/test4.jpg'),
(25.3, '/static/images/test5.jpg');

-- 插入系统状态数据
INSERT INTO system_status (component, status, error_message) VALUES
('ds18b20', 'online', NULL),
('camera', 'online', NULL),
('db', 'online', NULL);

-- 创建数据清理函数（可选）
CREATE OR REPLACE FUNCTION cleanup_old_data(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM sensor_data 
    WHERE timestamp < NOW() - INTERVAL '1 day' * days_to_keep;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- 创建自动清理任务（需要pg_cron扩展）
-- SELECT cron.schedule('cleanup-old-data', '0 2 * * *', 'SELECT cleanup_old_data(30);');

-- 验证数据
-- SELECT * FROM sensor_data ORDER BY timestamp DESC LIMIT 5;
-- SELECT * FROM latest_sensor_data;
-- SELECT * FROM sensor_statistics LIMIT 5;
-- SELECT * FROM system_status;

-- 数据库维护命令
-- \dt -- 查看所有表
-- \dv -- 查看所有视图
-- \di -- 查看所有索引
-- \du -- 查看所有用户