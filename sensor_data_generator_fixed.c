// Kunlun Sentinel Lab Monitor - 传感器数据生成器 (C)
// 功能：随机生成温度/光敏/摄像头像素矩阵，按固定间隔通过HTTP POST发送到后端 /api/ingest
// 数据格式：JSON，包含 device_id、timestamp_ms、temperature_c、light、frame(width/height/pixels)、checksum_frame
// 运行示例：
//   ./sensor_data_generator -h 127.0.0.1 -p 5000 -d device-001 -s 8 -i 1000
// 编译（ARM64）：
//   gcc -O2 -std=c11 sensor_data_generator.c -o sensor_data_generator -lm

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <time.h>
#include <sys/time.h>
#include <errno.h>
#include <math.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <getopt.h>
#if defined(__ARM_FEATURE_CRC32)
#include <arm_acle.h>
#endif

#define DEFAULT_INTERVAL_MS 1000
#define DEFAULT_SIZE 8
#define MAX_JSON_SIZE (1024 * 1024)

static unsigned long crc32_table[256];

static void crc32_init(void) {
    unsigned long polynomial = 0xEDB88320UL;
    for (unsigned long i = 0; i < 256; i++) {
        unsigned long c = i;
        for (int j = 0; j < 8; j++) {
            if (c & 1)
                c = polynomial ^ (c >> 1);
            else
                c >>= 1;
        }
        crc32_table[i] = c;
    }
}

static unsigned long crc32_compute(const unsigned char *buf, size_t len) {
#if defined(__ARM_FEATURE_CRC32)
    unsigned int c = 0xFFFFFFFFu;
    size_t i = 0;
    for (; i + 8 <= len; i += 8) {
        unsigned long long v;
        memcpy(&v, buf + i, 8);
        c = __crc32d(c, v);
    }
    for (; i < len; ++i) {
        c = __crc32b(c, buf[i]);
    }
    return (unsigned long)(c ^ 0xFFFFFFFFu);
#else
    unsigned long c = 0xFFFFFFFFUL;
    for (size_t i = 0; i < len; ++i) {
        c = crc32_table[(c ^ buf[i]) & 0xFF] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFUL;
#endif
}

static long long now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (long long)tv.tv_sec * 1000LL + (tv.tv_usec / 1000);
}

static void log_msg(const char *level, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    long long ts = now_ms();
    fprintf(stderr, "[%lld] %s ", ts, level);
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

static double rand_range_double(double min, double max) {
    double r = (double)rand() / (double)RAND_MAX;
    double v = min + r * (max - min);
    // 保留1位小数
    return floor(v * 10.0 + 0.5) / 10.0;
}

static int rand_range_int(int min, int max) {
    return min + rand() % (max - min + 1);
}

// 生成RGB像素矩阵（width*height，每像素3字节），返回分配的缓冲区（需free），并设置长度
static unsigned char *generate_frame_bytes(int width, int height, size_t *out_len) {
    size_t len = (size_t)width * (size_t)height * 3;
    unsigned char *buf = (unsigned char *)malloc(len);
    if (!buf) return NULL;
    for (size_t i = 0; i < len; ++i) {
        buf[i] = (unsigned char)rand_range_int(0, 255);
    }
    *out_len = len;
    return buf;
}

// 将像素矩阵编码为JSON的嵌套数组形式（pixels: [[{r,g,b},...],...]）
static int append_pixels_json(char *dst, size_t cap, const unsigned char *frame, int width, int height) {
    size_t pos = strlen(dst);
    // 如果前缀误以为 pixels 是字符串，移除末尾的引号，改为数组
    if (pos > 0 && dst[pos - 1] == '"') {
        pos -= 1;
        dst[pos] = '\0';
    }
    if (pos + 1 >= cap) return -1;
    dst[pos++] = '['; dst[pos] = '\0';
    for (int y = 0; y < height; ++y) {
        if (pos + 1 >= cap) return -1;
        dst[pos++] = '['; dst[pos] = '\0';
        for (int x = 0; x < width; ++x) {
            size_t idx = (size_t)(y * width + x) * 3;
            unsigned int r = frame[idx + 0];
            unsigned int g = frame[idx + 1];
            unsigned int b = frame[idx + 2];
            int n = snprintf(dst + pos, cap - pos,
                             "{\"r\":%u,\"g\":%u,\"b\":%u}%s",
                             r, g, b, (x == width - 1) ? "" : ",");
            if (n < 0 || (size_t)n >= cap - pos) return -1;
            pos += (size_t)n;
        }
        if (pos + 2 >= cap) return -1;
        dst[pos++] = ']';
        if (y != height - 1) dst[pos++] = ',';
        dst[pos] = '\0';
    }
    if (pos + 2 >= cap) return -1;
    dst[pos++] = ']';
    dst[pos] = '\0';
    return 0;
}

static int send_http_post(const char *host, int port, const char *path, const char *payload, size_t plen) {
    struct addrinfo hints, *res = NULL, *rp;
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    int err = getaddrinfo(host, port_str, &hints, &res);
    if (err != 0) {
        log_msg("ERROR", "DNS解析失败: %s", gai_strerror(err));
        return -1;
    }

    int sock = -1;
    for (rp = res; rp != NULL; rp = rp->ai_next) {
        sock = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (sock == -1) continue;
        if (connect(sock, rp->ai_addr, rp->ai_addrlen) == 0) break;
        close(sock);
        sock = -1;
    }
    freeaddrinfo(res);
    if (sock == -1) {
        log_msg("ERROR", "连接后端失败");
        return -1;
    }

    char header[1024];
    int hn = snprintf(header, sizeof(header),
                      "POST %s HTTP/1.1\r\n"
                      "Host: %s:%d\r\n"
                      "Content-Type: application/json\r\n"
                      "Connection: close\r\n"
                      "Content-Length: %zu\r\n\r\n",
                      path, host, port, plen);
    if (hn < 0) { close(sock); return -1; }

    ssize_t w1 = write(sock, header, (size_t)hn);
    ssize_t w2 = write(sock, payload, plen);
    if (w1 < 0 || w2 < 0) {
        log_msg("ERROR", "发送失败: %s", strerror(errno));
        close(sock);
        return -1;
    }

    // 读取响应（简单判断是否包含"200"）
    char resp[2048];
    ssize_t r = read(sock, resp, sizeof(resp) - 1);
    if (r > 0) {
        resp[r] = '\0';
        if (strstr(resp, " 200 ") == NULL && strstr(resp, " 200\r") == NULL) {
            log_msg("WARN", "后端非200响应: %.64s", resp);
        }
    }
    close(sock);
    return 0;
}

int main(int argc, char **argv) {
    const char *host = "127.0.0.1";
    int port = 5000;
    const char *device_id = "device-001";
    int size = DEFAULT_SIZE;
    int interval_ms = DEFAULT_INTERVAL_MS;

    // 解析简单参数
    int opt;
    while ((opt = getopt(argc, argv, "h:p:d:s:i:")) != -1) {
        switch (opt) {
            case 'h': host = optarg; break;
            case 'p': port = atoi(optarg); break;
            case 'd': device_id = optarg; break;
            case 's': size = atoi(optarg); break;
            case 'i': interval_ms = atoi(optarg); break;
            default:
                fprintf(stderr, "用法: %s -h host -p port -d device_id [-s 8|16] [-i ms]\n", argv[0]);
                return 1;
        }
    }
    if (size != 8 && size != 16) size = DEFAULT_SIZE;

    // 初始化随机与CRC
    srand((unsigned int)time(NULL));
    crc32_init();

    log_msg("INFO", "目标后端: http://%s:%d/api/ingest  设备ID=%s  尺寸=%dx%d  间隔=%dms",
         host, port, device_id, size, size, interval_ms);

    // 主循环
    for (;;) {
        // 1) 生成数据
        long long ts_ms = now_ms();
        double temp_c = rand_range_double(-20.0, 50.0);
        int light = rand_range_int(0, 1024);

        size_t frame_len = 0;
        unsigned char *frame = generate_frame_bytes(size, size, &frame_len);
        if (!frame) {
            log_msg("ERROR", "分配帧内存失败");
            break;
        }
        unsigned long checksum = crc32_compute(frame, frame_len);

        // 2) 构造JSON
        char *json = (char *)malloc(MAX_JSON_SIZE);
        if (!json) {
            free(frame);
            log_msg("ERROR", "分配JSON缓冲失败");
            break;
        }
        
        // Build JSON step by step to avoid long string literals
        int pos = snprintf(json, MAX_JSON_SIZE, "{");
        pos += snprintf(json + pos, MAX_JSON_SIZE - pos, "\"device_id\":\"%s\",", device_id);
        pos += snprintf(json + pos, MAX_JSON_SIZE - pos, "\"timestamp_ms\":%lld,", ts_ms);
        pos += snprintf(json + pos, MAX_JSON_SIZE - pos, "\"temperature_c\":%.1f,", temp_c);
        pos += snprintf(json + pos, MAX_JSON_SIZE - pos, "\"light\":%d,", light);
        pos += snprintf(json + pos, MAX_JSON_SIZE - pos, "\"frame\":{\"width\":%d,\"height\":%d,\"pixels\":", size, size);
        if (pos < 0) { free(frame); free(json); break; }

        // 追加 pixels 数组
        if (append_pixels_json(json, MAX_JSON_SIZE, frame, size, size) != 0) {
            log_msg("ERROR", "像素JSON超长");
            free(frame); free(json); break;
        }

        // 追加 frame 结束与校验
        size_t json_pos = strlen(json);
        int m = snprintf(json + json_pos, MAX_JSON_SIZE - json_pos,
                         "},\"checksum_frame\":\"%08lx\"}", checksum);
        if (m < 0) { free(frame); free(json); break; }

        // 3) 发送HTTP POST
        if (send_http_post(host, port, "/api/ingest", json, strlen(json)) != 0) {
            // 简单重试：指数退避至5秒
            int backoff_ms = 500;
            for (int attempt = 0; attempt < 5; ++attempt) {
                log_msg("WARN", "发送失败，重试 #%d，等待 %dms", attempt + 1, backoff_ms);
                usleep(backoff_ms * 1000);
                if (send_http_post(host, port, "/api/ingest", json, strlen(json)) == 0) break;
                backoff_ms = (backoff_ms < 5000) ? backoff_ms * 2 : 5000;
            }
        } else {
            log_msg("INFO", "已发送：temp=%.1f light=%d size=%dx%d", temp_c, light, size, size);
        }

        free(frame);
        free(json);

        // 4) 间隔
        usleep(interval_ms * 1000);
    }

    return 0;
}