#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <fcntl.h>
#include <termios.h>
#include <errno.h>

// 串口配置函数
// 作用：设置串口波特率为9600，8数据位，无校验，1停止位 (8N1)，并配置为原始模式（Raw Mode）
static int configure_serial(int fd) {
    struct termios tty;
    if (tcgetattr(fd, &tty) != 0) {
        perror("tcgetattr");
        return -1;
    }

    // 设置输入输出波特率为 9600
    cfsetospeed(&tty, B9600);
    cfsetispeed(&tty, B9600);

    // 控制模式标志
    tty.c_cflag &= ~PARENB; // 无校验位
    tty.c_cflag &= ~CSTOPB; // 1个停止位
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;     // 8个数据位
    tty.c_cflag |= CREAD | CLOCAL; // 打开接收器，忽略调制解调器控制线

    // 本地模式标志：设置为非规范模式（Raw Mode），不进行行缓冲
    tty.c_lflag &= ~ICANON;
    tty.c_lflag &= ~ECHO;   // 关闭回显
    tty.c_lflag &= ~ECHOE;
    tty.c_lflag &= ~ISIG;   // 关闭信号字符处理

    // 输入模式标志：关闭软件流控
    tty.c_iflag &= ~(IXON | IXOFF | IXANY);
    tty.c_iflag &= ~(IGNBRK|BRKINT|PARMRK|ISTRIP|INLCR|IGNCR|ICRNL); // 禁用特殊字符处理

    // 输出模式标志：原始输出
    tty.c_oflag &= ~OPOST;
    tty.c_oflag &= ~ONLCR;

    // 设置读取超时
    tty.c_cc[VTIME] = 10;    // 等待 1.0 秒
    tty.c_cc[VMIN] = 0;

    if (tcsetattr(fd, TCSANOW, &tty) != 0) {
        perror("tcsetattr");
        return -1;
    }
    return 0;
}

// 串口读取函数
// 作用：从串口读取一行数据（以换行符结尾），并解析出温度值
// 格式预期：Arduino 发送 "T:25.50\n"
static int read_serial_temp(int fd, double *out) {
    char buf[64];
    int n = 0;
    char c;
    
    // 逐字节读取，直到遇到换行符或缓冲区满
    while (n < sizeof(buf) - 1) {
        int rd = read(fd, &c, 1);
        if (rd > 0) {
            if (c == '\n' || c == '\r') {
                if (n > 0) break; // 读到行尾，且已有数据
                continue; // 忽略开头的空行
            }
            buf[n++] = c;
        } else if (rd == 0) {
            return -1; // 超时或无数据
        } else {
            return -1; // 错误
        }
    }
    buf[n] = '\0'; // 字符串结束符

    // 解析数据：查找 "T:" 前缀
    char *p = strstr(buf, "T:");
    if (p) {
        *out = atof(p + 2); // 跳过 "T:" 转换为浮点数
        return 0;
    }
    return -1; // 格式不匹配
}

static long now_ms() {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long)tv.tv_sec * 1000L + (long)tv.tv_usec / 1000L;
}

int main() {
    const char *host = getenv("RELAY_HOST");
    const char *port_s = getenv("RELAY_PORT");
    if (!host) host = "127.0.0.1";
    int port = port_s ? atoi(port_s) : 5555;

    // 创建 UDP 套接字用于发送数据
    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in servaddr;
    memset(&servaddr, 0, sizeof(servaddr));
    servaddr.sin_family = AF_INET;
    servaddr.sin_port = htons(port);
    inet_pton(AF_INET, host, &servaddr.sin_addr);

    printf("Starting Serial Temperature Collector...\n");
    printf("Target Relay: %s:%d\n", host, port);

    // 尝试打开串口设备
    // 扫描列表：Arduino USB, USB转串口, 板载串口
    // 优先尝试 USB 设备，最后尝试板载串口
    const char *dev_list[] = {
        "/dev/ttyACM0", // Arduino Uno USB (优先)
        "/dev/ttyUSB0", // USB-TTL
        "/dev/ttyAMA2", // 板载串口 2 (Pin 11/36)
        "/dev/ttyAMA1", // 板载串口 1
        "/dev/ttyS0",   // 板载 Mini UART
        NULL
    };

    int fd = -1;
    const char *serial_dev = NULL;

    for (int i = 0; dev_list[i] != NULL; i++) {
        if (access(dev_list[i], F_OK) == 0) {
            serial_dev = dev_list[i];
            printf("Found device: %s\n", serial_dev);
            fd = open(serial_dev, O_RDWR | O_NOCTTY | O_SYNC);
            if (fd >= 0) {
                printf("Successfully opened: %s\n", serial_dev);
                configure_serial(fd);
                break;
            } else {
                perror("Open failed");
            }
        }
    }

    if (fd < 0) {
        printf("No suitable serial device found or permission denied.\n");
        printf("Hint: Check connections and permissions (sudo usermod -aG dialout $USER)\n");
    }

    // 主循环：不断读取串口并发送 UDP
    while (1) {
        double temp = 0.0;
        int success = 0;

        if (fd >= 0) {
            if (read_serial_temp(fd, &temp) == 0) {
                success = 1;
            } else {
                // 读取失败，可能是串口断开
                printf("Serial read failed/timeout.\n");
            }
        }

        // 如果读取成功，构建 JSON 并发送
        if (success) {
            char json[256];
            long ts = now_ms();
            // 构造符合项目协议的 JSON 数据包
            snprintf(json, sizeof(json), 
                "{\"device_id\": \"temp-arduino-1\", \"timestamp_ms\": %ld, \"temperature_c\": %.2f}", 
                ts, temp);
            
            sendto(sockfd, json, strlen(json), 0, (const struct sockaddr *)&servaddr, sizeof(servaddr));
            printf("Sent: %s\n", json);
        }

        // 稍微休眠，避免 CPU 占用过高（Arduino 每秒发一次，这里稍微快点读也没关系）
        usleep(100000); // 100ms
    }

    if (fd >= 0) close(fd);
    close(sockfd);
    return 0;
}