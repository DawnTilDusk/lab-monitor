-- 昆仑哨兵·实验室多模态监控系统
-- 数据库初始化脚本 - 重构版（分表设计，幂等操作）
-- 适用于openGauss 5.0.0

-- 创建数据库（如果不存在）
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'lab_monitor') THEN
        EXECUTE 'CREATE DATABASE lab_monitor
            WITH 
            OWNER = labuser
            ENCODING = ''UTF8''
            LC_COLLATE = ''en_US.UTF-8''
            LC_CTYPE = ''en_US.UTF-8''
            TABLESPACE = pg_default
            CONNECTION LIMIT = -1';
    END IF;
END $$;

-- 授予权限
GRANT CONNECT, TEMPORARY ON DATABASE lab_monitor TO PUBLIC;
GRANT ALL ON DATABASE lab_monitor TO labuser;

-- 使用数据库
\c lab_monitor;

-- ==========================================
-- 创建基础表结构（如果不存在）
-- ==========================================

-- A. 温度数据表
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'temperature_data') THEN
        CREATE TABLE temperature_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            value REAL NOT NULL,                    -- 温度值
            device_id VARCHAR(50) DEFAULT 'temp_main', -- 设备ID
            unit VARCHAR(10) DEFAULT 'C',           -- 单位
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END $$;

-- B. 图像数据表
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'image_data') THEN
        CREATE TABLE image_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            image_path TEXT NOT NULL,               -- 图片路径
            width INTEGER,                          -- 图像宽度
            height INTEGER,                         -- 图像高度
            device_id VARCHAR(50) DEFAULT 'camera_main', -- 摄像头ID
            file_size BIGINT,                       -- 文件大小
            created_at TIMESTAMPTZ DEFAULT NOW(),
            bubble BOOLEAN DEFAULT FALSE            -- 是否为定时生成的图片
        );
    END IF;
END $$;

-- C. 光敏数据表
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'light_data') THEN
        CREATE TABLE light_data (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMPTZ DEFAULT NOW(),
            value INTEGER,                          -- 光敏值
            device_id VARCHAR(50) DEFAULT 'light_main', -- 设备ID
            unit VARCHAR(20) DEFAULT 'lux',         -- 单位
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END $$;

-- D. 模型输出表
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'model_outputs') THEN
        CREATE TABLE model_outputs (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL,             -- 模型名称
            output TEXT NOT NULL,                   -- 模型输出内容
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END $$;

-- E. 系统状态表
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'system_status') THEN
        CREATE TABLE system_status (
            id SERIAL PRIMARY KEY,
            component VARCHAR(50) NOT NULL,         -- 组件名称(ds18b20/camera/db)
            status VARCHAR(20) NOT NULL,            -- 组件状态(online/offline/error)
            last_check TIMESTAMPTZ DEFAULT NOW(),
            error_message TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    END IF;
END $$;

-- ==========================================
-- 创建索引（如果不存在）
-- ==========================================

DO $$
BEGIN
    -- 温度数据索引
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_temp_timestamp') THEN
        CREATE INDEX idx_temp_timestamp ON temperature_data(timestamp DESC);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_temp_device') THEN
        CREATE INDEX idx_temp_device ON temperature_data(device_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_temp_value') THEN
        CREATE INDEX idx_temp_value ON temperature_data(value);
    END IF;
    
    -- 图像数据索引
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_image_timestamp') THEN
        CREATE INDEX idx_image_timestamp ON image_data(timestamp DESC);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_image_device') THEN
        CREATE INDEX idx_image_device ON image_data(device_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_image_bubble') THEN
        CREATE INDEX idx_image_bubble ON image_data(bubble);
    END IF;
    
    -- 光敏数据索引
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_light_timestamp') THEN
        CREATE INDEX idx_light_timestamp ON light_data(timestamp DESC);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_light_device') THEN
        CREATE INDEX idx_light_device ON light_data(device_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_light_value') THEN
        CREATE INDEX idx_light_value ON light_data(value);
    END IF;
    
    -- 模型输出索引
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_model_name') THEN
        CREATE INDEX idx_model_name ON model_outputs(name);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_model_created_at') THEN
        CREATE INDEX idx_model_created_at ON model_outputs(created_at DESC);
    END IF;
    
    -- 系统状态索引
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_system_component') THEN
        CREATE INDEX idx_system_component ON system_status(component);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_system_status') THEN
        CREATE INDEX idx_system_status ON system_status(status);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_system_last_check') THEN
        CREATE INDEX idx_system_last_check ON system_status(last_check DESC);
    END IF;
    
END $$;

-- ==========================================
-- 添加表注释（如果不存在）
-- ==========================================

DO $$
BEGIN
    -- 温度数据表注释
    IF NOT EXISTS (SELECT 1 FROM pg_description d JOIN pg_class c ON d.objoid = c.oid WHERE c.relname = 'temperature_data' AND d.objsubid = 0) THEN
        COMMENT ON TABLE temperature_data IS '温度传感器数据表';
        COMMENT ON COLUMN temperature_data.id IS '数据记录ID';
        COMMENT ON COLUMN temperature_data.timestamp IS '温度采集时间戳';
        COMMENT ON COLUMN temperature_data.value IS '温度值(摄氏度)';
        COMMENT ON COLUMN temperature_data.device_id IS '传感器设备ID';
        COMMENT ON COLUMN temperature_data.unit IS '温度单位';
        COMMENT ON COLUMN temperature_data.created_at IS '记录创建时间';
    END IF;
    
    -- 图像数据表注释
    IF NOT EXISTS (SELECT 1 FROM pg_description d JOIN pg_class c ON d.objoid = c.oid WHERE c.relname = 'image_data' AND d.objsubid = 0) THEN
        COMMENT ON TABLE image_data IS '图像传感器数据表';
        COMMENT ON COLUMN image_data.id IS '数据记录ID';
        COMMENT ON COLUMN image_data.timestamp IS '图像采集时间戳';
        COMMENT ON COLUMN image_data.image_path IS '图像文件路径';
        COMMENT ON COLUMN image_data.width IS '图像宽度';
        COMMENT ON COLUMN image_data.height IS '图像高度';
        COMMENT ON COLUMN image_data.device_id IS '摄像头设备ID';
        COMMENT ON COLUMN image_data.file_size IS '文件大小(字节)';
        COMMENT ON COLUMN image_data.bubble IS '是否为定时生成的图片';
        COMMENT ON COLUMN image_data.created_at IS '记录创建时间';
    END IF;
    
    -- 光敏数据表注释
    IF NOT EXISTS (SELECT 1 FROM pg_description d JOIN pg_class c ON d.objoid = c.oid WHERE c.relname = 'light_data' AND d.objsubid = 0) THEN
        COMMENT ON TABLE light_data IS '光敏传感器数据表';
        COMMENT ON COLUMN light_data.id IS '数据记录ID';
        COMMENT ON COLUMN light_data.timestamp IS '光敏采集时间戳';
        COMMENT ON COLUMN light_data.value IS '光敏值(照度lx或原始值)';
        COMMENT ON COLUMN light_data.device_id IS '光敏传感器设备ID';
        COMMENT ON COLUMN light_data.unit IS '光敏单位';
        COMMENT ON COLUMN light_data.created_at IS '记录创建时间';
    END IF;
    
    -- 模型输出表注释
    IF NOT EXISTS (SELECT 1 FROM pg_description d JOIN pg_class c ON d.objoid = c.oid WHERE c.relname = 'model_outputs' AND d.objsubid = 0) THEN
        COMMENT ON TABLE model_outputs IS 'AI模型输出数据表';
        COMMENT ON COLUMN model_outputs.id IS '输出记录ID';
        COMMENT ON COLUMN model_outputs.name IS '模型名称';
        COMMENT ON COLUMN model_outputs.output IS '模型输出内容(JSON格式)';
        COMMENT ON COLUMN model_outputs.created_at IS '记录创建时间';
    END IF;
    
    -- 系统状态表注释
    IF NOT EXISTS (SELECT 1 FROM pg_description d JOIN pg_class c ON d.objoid = c.oid WHERE c.relname = 'system_status' AND d.objsubid = 0) THEN
        COMMENT ON TABLE system_status IS '系统组件状态表';
        COMMENT ON COLUMN system_status.id IS '状态记录ID';
        COMMENT ON COLUMN system_status.component IS '组件名称(ds18b20/camera/db/model)';
        COMMENT ON COLUMN system_status.status IS '组件状态(online/offline/error)';
        COMMENT ON COLUMN system_status.last_check IS '最后检查时间';
        COMMENT ON COLUMN system_status.error_message IS '错误信息';
        COMMENT ON COLUMN system_status.created_at IS '记录创建时间';
    END IF;
END $$;

-- ==========================================
-- 数据迁移：从原表到新表（如果需要）
-- ==========================================

-- 临时禁用数据迁移以防止启动卡死
-- DO $$
-- BEGIN
--     IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'sensor_data') THEN
--         IF (SELECT COUNT(*) FROM sensor_data) > 0
--            AND (SELECT COUNT(*) FROM temperature_data) = 0 
--            AND (SELECT COUNT(*) FROM image_data) = 0 
--            AND (SELECT COUNT(*) FROM light_data) = 0 THEN
--             INSERT INTO temperature_data (timestamp, value, device_id, created_at)
--             SELECT timestamp, temperature, 'temp_main', created_at
--             FROM sensor_data
--             WHERE temperature IS NOT NULL;
--
--             INSERT INTO image_data (timestamp, image_path, device_id, bubble, created_at)
--             SELECT timestamp, image_path, 'camera_main', 
--             CASE WHEN bubble_count = 1 THEN TRUE ELSE FALSE END, created_at
--             FROM sensor_data
--             WHERE image_path IS NOT NULL AND TRIM(image_path) != '';
--
--             INSERT INTO light_data (timestamp, value, device_id, created_at)
--             SELECT timestamp, light, 'light_main', created_at
--             FROM sensor_data
--             WHERE light IS NOT NULL;
--
--             RAISE NOTICE '已从原始表迁移数据到新表';
--         END IF;
--     END IF;
-- END $$;

-- ==========================================
-- 创建视图（如果不存在）
-- ==========================================

-- 创建视图模拟原表结构（用于向后兼容）
DO $$
BEGIN
    DROP VIEW IF EXISTS sensor_data_compatible;
    CREATE VIEW sensor_data_compatible AS
    SELECT 
        COALESCE(t.id, i.id, l.id) as id,
        COALESCE(t.timestamp, i.timestamp, l.timestamp) as timestamp,
        t.value as temperature,
        i.image_path,
        l.value as light,
        COALESCE(i.bubble, false)::int as bubble_count,
        GREATEST(COALESCE(t.created_at, 'epoch'), 
                 COALESCE(i.created_at, 'epoch'), 
                 COALESCE(l.created_at, 'epoch')) as created_at
    FROM temperature_data t
    FULL OUTER JOIN image_data i ON ABS(EXTRACT(EPOCH FROM (t.timestamp - i.timestamp))) < 60
    FULL OUTER JOIN light_data l ON ABS(EXTRACT(EPOCH FROM (GREATEST(COALESCE(t.timestamp, i.timestamp)) - l.timestamp))) < 60
    ORDER BY COALESCE(t.timestamp, i.timestamp, l.timestamp) DESC;
END $$;

-- 温度统计视图
DO $$
BEGIN
    DROP VIEW IF EXISTS temperature_statistics;
    CREATE VIEW temperature_statistics AS
    SELECT 
        timestamp::date AS date,
        COUNT(*) AS record_count,
        ROUND(AVG(value), 2) AS avg_temperature,
        ROUND(MIN(value), 2) AS min_temperature,
        ROUND(MAX(value), 2) AS max_temperature,
        MIN(timestamp) as first_record,
        MAX(timestamp) as last_record
    FROM temperature_data 
    GROUP BY timestamp::date
    ORDER BY date DESC;
END $$;

-- 光敏统计视图
DO $$
BEGIN
    DROP VIEW IF EXISTS light_statistics;
    CREATE VIEW light_statistics AS
    SELECT 
        timestamp::date AS date,
        COUNT(*) AS record_count,
        AVG(value) AS avg_light,
        MIN(value) AS min_light,
        MAX(value) AS max_light,
        MIN(timestamp) as first_record,
        MAX(timestamp) as last_record
    FROM light_data 
    GROUP BY timestamp::date
    ORDER BY date DESC;
END $$;

-- 最新数据视图
DO $$
BEGIN
    DROP VIEW IF EXISTS latest_sensor_data;
    CREATE VIEW latest_sensor_data AS
    SELECT 
        t.timestamp,
        t.value as temperature,
        i.image_path,
        l.value as light,
        CASE 
            WHEN t.value < 20 THEN '低温'
            WHEN t.value > 30 THEN '高温'
            WHEN t.value IS NOT NULL THEN '正常'
            ELSE '未知'
        END as temp_status
    FROM 
        (SELECT timestamp, value FROM temperature_data ORDER BY timestamp DESC LIMIT 1) t
    LEFT JOIN 
        (SELECT timestamp, image_path FROM image_data ORDER BY timestamp DESC LIMIT 1) i
        ON ABS(EXTRACT(EPOCH FROM (t.timestamp - i.timestamp))) < 300
    LEFT JOIN 
        (SELECT timestamp, value FROM light_data ORDER BY timestamp DESC LIMIT 1) l
        ON ABS(EXTRACT(EPOCH FROM (t.timestamp - l.timestamp))) < 300;
END $$;

-- ==========================================
-- 数据清理函数
-- ==========================================

-- 清理旧数据函数
CREATE OR REPLACE FUNCTION cleanup_old_data(days_to_keep INTEGER DEFAULT 30)
RETURNS TABLE(deleted_tables TEXT, deleted_count INTEGER) AS $$
DECLARE
    temp_deleted INTEGER := 0;
    image_deleted INTEGER := 0;
    light_deleted INTEGER := 0;
    cutoff_date TIMESTAMPTZ;
BEGIN
    cutoff_date := NOW() - INTERVAL '1 day' * days_to_keep;
    
    -- 清理温度数据
    DELETE FROM temperature_data 
    WHERE timestamp < cutoff_date;
    GET DIAGNOSTICS temp_deleted = ROW_COUNT;
    
    -- 清理图像数据
    DELETE FROM image_data 
    WHERE timestamp < cutoff_date;
    GET DIAGNOSTICS image_deleted = ROW_COUNT;
    
    -- 清理光敏数据
    DELETE FROM light_data 
    WHERE timestamp < cutoff_date;
    GET DIAGNOSTICS light_deleted = ROW_COUNT;
    
    -- 返回结果
    RETURN QUERY
    SELECT 'temperature_data'::TEXT as table_name, temp_deleted as count
    UNION ALL
    SELECT 'image_data'::TEXT as table_name, image_deleted as count
    UNION ALL
    SELECT 'light_data'::TEXT as table_name, light_deleted as count;
END;
$$ LANGUAGE plpgsql;

-- ==========================================
-- 权限授予
-- ==========================================

-- 授予基础权限给labuser
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO labuser;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO labuser;

-- 授予视图查询权限
GRANT SELECT ON temperature_statistics TO labuser;
GRANT SELECT ON light_statistics TO labuser;
GRANT SELECT ON latest_sensor_data TO labuser;
GRANT SELECT ON sensor_data_compatible TO labuser;

-- ==========================================
-- 条件插入测试数据（幂等操作）
-- ==========================================

DO $$
BEGIN
    -- 插入温度测试数据（如果表为空）
    IF (SELECT COUNT(*) FROM temperature_data) = 0 THEN
        INSERT INTO temperature_data (value, device_id) VALUES
        (25.2, 'temp_main'),
        (25.5, 'temp_main'),
        (24.8, 'temp_main'),
        (25.1, 'temp_main'),
        (25.3, 'temp_main');
        RAISE NOTICE '已插入温度测试数据';
    ELSE
        RAISE NOTICE '温度表已有数据，跳过测试数据插入';
    END IF;
    
    -- 插入图像测试数据（如果表为空）
    IF (SELECT COUNT(*) FROM image_data) = 0 THEN
        INSERT INTO image_data (image_path, device_id) VALUES
        ('/static/images/test1.jpg', 'camera_main'),
        ('/static/images/test2.jpg', 'camera_main'),
        ('/static/images/test3.jpg', 'camera_main'),
        ('/static/images/test4.jpg', 'camera_main'),
        ('/static/images/test5.jpg', 'camera_main');
        RAISE NOTICE '已插入图像测试数据';
    ELSE
        RAISE NOTICE '图像表已有数据，跳过测试数据插入';
    END IF;
    
    -- 插入光敏测试数据（如果表为空）
    IF (SELECT COUNT(*) FROM light_data) = 0 THEN
        INSERT INTO light_data (value, device_id) VALUES
        (450, 'light_main'),
        (460, 'light_main'),
        (440, 'light_main'),
        (455, 'light_main'),
        (465, 'light_main');
        RAISE NOTICE '已插入光敏测试数据';
    ELSE
        RAISE NOTICE '光敏表已有数据，跳过测试数据插入';
    END IF;
    
    -- 插入系统状态测试数据（如果表为空）
    IF (SELECT COUNT(*) FROM system_status) = 0 THEN
        INSERT INTO system_status (component, status, error_message) VALUES
        ('ds18b20', 'online', NULL),
        ('camera', 'online', NULL),
        ('db', 'online', NULL),
        ('model', 'online', NULL);
        RAISE NOTICE '已插入系统状态测试数据';
    ELSE
        RAISE NOTICE '系统状态表已有数据，跳过测试数据插入';
    END IF;
END $$;

-- ==========================================
-- 验证查询
-- ==========================================

-- 验证数据插入情况
DO $$
BEGIN
    RAISE NOTICE '数据表统计:';
    RAISE NOTICE '  temperature_data: % 条记录', (SELECT COUNT(*) FROM temperature_data);
    RAISE NOTICE '  image_data: % 条记录', (SELECT COUNT(*) FROM image_data);
    RAISE NOTICE '  light_data: % 条记录', (SELECT COUNT(*) FROM light_data);
    RAISE NOTICE '  system_status: % 条记录', (SELECT COUNT(*) FROM system_status);
    RAISE NOTICE '  model_outputs: % 条记录', (SELECT COUNT(*) FROM model_outputs);
END $$;

-- 验证查询示例（取消注释以执行）
-- SELECT * FROM latest_sensor_data LIMIT 1;
-- SELECT * FROM temperature_statistics LIMIT 5;
-- SELECT * FROM light_statistics LIMIT 5;
-- SELECT * FROM sensor_data_compatible ORDER BY timestamp DESC LIMIT 5;

-- 验证函数
-- SELECT * FROM cleanup_old_data(30);
