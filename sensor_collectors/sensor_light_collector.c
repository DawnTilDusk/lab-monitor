#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <errno.h>

// BH1750 I2C 地址 (默认 ADDR 接地时为 0x23)
#define BH1750_ADDR 0x23

// BH1750 指令
#define CMD_POWER_ON  0x01
#define CMD_RESET     0x07
#define CMD_H_RES_MODE 0x10 // 高分辨率模式 1 lux

// I2C 设备文件路径 (假设与 AHT10 共用 I2C-7)
// 如果使用其他接口，请修改此处或通过环境变量覆盖
#define DEFAULT_I2C_DEV "/dev/i2c-7"

static int i2c_fd = -1;

// 获取当前毫秒时间戳
static long now_ms() {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long)tv.tv_sec * 1000L + (long)tv.tv_usec / 1000L;
}

// 初始化 BH1750
static int bh1750_init(const char *dev_path) {
    if (i2c_fd < 0) {
        i2c_fd = open(dev_path, O_RDWR);
        if (i2c_fd < 0) {
            fprintf(stderr, "Error: Failed to open I2C bus %s: %s\n", dev_path, strerror(errno));
            return -1;
        }
        
        if (ioctl(i2c_fd, I2C_SLAVE, BH1750_ADDR) < 0) {
            fprintf(stderr, "Error: Failed to acquire bus access to 0x%x: %s\n", BH1750_ADDR, strerror(errno));
            return -1;
        }
    }

    // 发送 Power On
    unsigned char cmd = CMD_POWER_ON;
    if (write(i2c_fd, &cmd, 1) != 1) {
        perror("Failed to send Power On command");
        return -1;
    }
    
    // 发送 Reset (可选)
    // cmd = CMD_RESET;
    // write(i2c_fd, &cmd, 1);

    // 设置为连续高分辨率模式
    cmd = CMD_H_RES_MODE;
    if (write(i2c_fd, &cmd, 1) != 1) {
        perror("Failed to set measurement mode");
        return -1;
    }
    
    // 等待测量完成 (第一次至少需要 180ms)
    usleep(180000);
    return 0;
}

// 读取光照强度 (Lux)
static int bh1750_read(double *lux) {
    if (i2c_fd < 0) return -1;

    unsigned char buf[2];
    if (read(i2c_fd, buf, 2) != 2) {
        perror("Failed to read data from BH1750");
        return -1;
    }

    // 计算 Lux: (HighByte << 8 | LowByte) / 1.2
    int val = ((int)buf[0] << 8) | buf[1];
    *lux = (double)val / 1.2;

    return 0;
}

int main() {
    // 配置相关环境变量
    const char *relay_host = getenv("RELAY_HOST");
    const char *relay_port_s = getenv("RELAY_PORT");
    const char *i2c_dev = getenv("I2C_DEVICE"); // 允许通过环境变量覆盖 I2C 设备

    if (!relay_host) relay_host = "127.0.0.1";
    int relay_port = relay_port_s ? atoi(relay_port_s) : 9999;
    if (!i2c_dev) i2c_dev = DEFAULT_I2C_DEV;

    // 创建 UDP socket
    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("socket creation failed");
        return 1;
    }

    struct sockaddr_in servaddr;
    memset(&servaddr, 0, sizeof(servaddr));
    servaddr.sin_family = AF_INET;
    servaddr.sin_port = htons(relay_port);
    servaddr.sin_addr.s_addr = inet_addr(relay_host);

    printf("BH1750 (GY-30) Collector started.\n");
    printf("Target: %s:%d\n", relay_host, relay_port);
    printf("I2C Device: %s, Address: 0x%x\n", i2c_dev, BH1750_ADDR);

    // 初始化传感器
    if (bh1750_init(i2c_dev) < 0) {
        fprintf(stderr, "Warning: BH1750 init failed, will retry in loop...\n");
    }

    char json[256];
    while (1) {
        double lux = 0.0;
        int success = 0;

        if (bh1750_read(&lux) == 0) {
            success = 1;
            printf("Read BH1750: Light=%.2f Lux\n", lux);
        } else {
            // 如果读取失败，尝试重新初始化
            // 可能是 I2C 总线临时故障或传感器重新连接
            fprintf(stderr, "Read failed, retrying init...\n");
            close(i2c_fd);
            i2c_fd = -1;
            usleep(500000); // 等待 0.5s 再重试
            bh1750_init(i2c_dev);
        }

        if (success) {
            long ts = now_ms();
            // 组装 JSON
            // 格式: {"device_id": "bh1750-i2c", "timestamp_ms": 123, "light": 150.5}
            // 注意：这里我们使用 'light' 字段，类型为数字 (之前是 0/1，现在是 float)
            snprintf(json, sizeof(json), 
                "{\"device_id\": \"bh1750-i2c-7\", \"timestamp_ms\": %ld, \"light\": %.2f}", 
                ts, lux);

            sendto(sockfd, json, strlen(json), 0, (const struct sockaddr *)&servaddr, sizeof(servaddr));
        }

        // 采样间隔 1秒
        sleep(1);
    }

    if (i2c_fd >= 0) close(i2c_fd);
    close(sockfd);
    return 0;
}
