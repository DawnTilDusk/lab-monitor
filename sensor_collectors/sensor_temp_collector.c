#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <errno.h>

// AHT10 I2C 地址
#define AHT10_ADDR 0x38
// AHT10 命令
#define CMD_INIT 0xE1
#define CMD_MEASURE 0xAC
#define CMD_SOFT_RESET 0xBA

// I2C 设备文件路径，I2C7 对应 /dev/i2c-7
#define I2C_DEV_PATH "/dev/i2c-7"

static int i2c_fd = -1;

// 获取当前毫秒时间戳
static long now_ms() {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long)tv.tv_sec * 1000L + (long)tv.tv_usec / 1000L;
}

// 初始化 AHT10
static int aht10_init() {
    if (i2c_fd < 0) {
        i2c_fd = open(I2C_DEV_PATH, O_RDWR);
        if (i2c_fd < 0) {
            perror("Failed to open I2C bus");
            return -1;
        }
        if (ioctl(i2c_fd, I2C_SLAVE, AHT10_ADDR) < 0) {
            perror("Failed to acquire bus access and/or talk to slave");
            return -1;
        }
    }

    // 发送初始化命令: 0xE1 0x08 0x00
    unsigned char cmd[3] = {CMD_INIT, 0x08, 0x00};
    if (write(i2c_fd, cmd, 3) != 3) {
        perror("Failed to send init command");
        return -1;
    }
    
    usleep(50000); // 等待 50ms
    return 0;
}

// 读取温湿度
static int aht10_read(double *temp, double *hum) {
    if (i2c_fd < 0) {
        if (aht10_init() < 0) return -1;
    }

    // 1. 发送触发测量命令: 0xAC 0x33 0x00
    unsigned char cmd[3] = {CMD_MEASURE, 0x33, 0x00};
    if (write(i2c_fd, cmd, 3) != 3) {
        perror("Failed to send measure command");
        // 尝试重新初始化
        close(i2c_fd);
        i2c_fd = -1;
        return -1;
    }

    // 2. 等待测量完成 (手册建议 >75ms)
    usleep(80000);

    // 3. 读取 6 字节数据
    // Byte 0: State
    // Byte 1: Hum [7:0]
    // Byte 2: Hum [7:0]
    // Byte 3: Hum [7:4] | Temp [19:16]
    // Byte 4: Temp [15:8]
    // Byte 5: Temp [7:0]
    unsigned char data[6];
    if (read(i2c_fd, data, 6) != 6) {
        perror("Failed to read data");
        return -1;
    }

    // 4. 检查状态位 (Bit 3: Calibrated, Bit 7: Busy)
    if ((data[0] & 0x08) == 0) {
        // 未校准，尝试重新初始化
        aht10_init();
        return -1;
    }

    // 5. 解析数据
    unsigned long raw_hum = ((unsigned long)data[1] << 12) | ((unsigned long)data[2] << 4) | ((unsigned long)(data[3] & 0xF0) >> 4);
    unsigned long raw_temp = ((unsigned long)(data[3] & 0x0F) << 16) | ((unsigned long)data[4] << 8) | (unsigned long)data[5];

    *hum = ((double)raw_hum / 1048576.0) * 100.0;
    *temp = ((double)raw_temp / 1048576.0) * 200.0 - 50.0;

    return 0;
}

int main() {
    const char *relay_host = getenv("RELAY_HOST");
    const char *relay_port_s = getenv("RELAY_PORT");
    if (!relay_host) relay_host = "127.0.0.1";
    int relay_port = relay_port_s ? atoi(relay_port_s) : 9999;

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

    printf("AHT10 Collector started. Target: %s:%d, Device: %s\n", relay_host, relay_port, I2C_DEV_PATH);

    // 尝试初始化
    if (aht10_init() < 0) {
        fprintf(stderr, "Warning: AHT10 init failed, will retry in loop\n");
    }

    char json[256];
    while (1) {
        double temp = 0.0;
        double hum = 0.0;
        int success = 0;

        if (aht10_read(&temp, &hum) == 0) {
            // 简单过滤无效值
            if (temp > -40.0 && temp < 85.0) {
                success = 1;
                printf("Read AHT10: Temp=%.2f C, Hum=%.2f %%\n", temp, hum);
            }
        } else {
            fprintf(stderr, "Failed to read AHT10\n");
        }

        if (success) {
            long ts = now_ms();
            // 组装 JSON
            // 注意：这里我们同时发送温度和湿度，虽然目前的后端主要用温度
            // 格式: {"device_id": "aht10-i2c", "timestamp_ms": 123, "temperature_c": 25.5, "humidity": 60.0}
            snprintf(json, sizeof(json), 
                "{\"device_id\": \"aht10-i2c-7\", \"timestamp_ms\": %ld, \"temperature_c\": %.2f, \"humidity\": %.2f}", 
                ts, temp, hum);

            sendto(sockfd, json, strlen(json), 0, (const struct sockaddr *)&servaddr, sizeof(servaddr));
        }

        // 采样间隔 2秒
        sleep(1);
    }

    if (i2c_fd >= 0) close(i2c_fd);
    close(sockfd);
    return 0;
}
