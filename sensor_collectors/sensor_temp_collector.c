#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <glob.h>

static int read_ds18b20(double *out) {
    glob_t g;
    int rc = glob("/sys/bus/w1/devices/28-*/w1_slave", 0, NULL, &g);
    if (rc != 0 || g.gl_pathc == 0) {
        globfree(&g);
        return -1;
    }
    const char *path = g.gl_pathv[0];
    FILE *f = fopen(path, "r");
    if (!f) {
        globfree(&g);
        return -1;
    }
    char line1[128];
    char line2[128];
    if (!fgets(line1, sizeof(line1), f) || !fgets(line2, sizeof(line2), f)) {
        fclose(f);
        globfree(&g);
        return -1;
    }
    fclose(f);
    globfree(&g);
    if (strstr(line1, "YES") == NULL) return -1;
    char *p = strstr(line2, "t=");
    if (!p) return -1;
    long t_raw = atol(p + 2);
    *out = ((double)t_raw) / 1000.0;
    return 0;
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
    int port = port_s ? atoi(port_s) : 9999;

    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) return 1;
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = inet_addr(host);

    char buf[512];
    for (;;) {
        long ts = now_ms();
        double val = 0.0;
        if (read_ds18b20(&val) != 0) {
            usleep(1000000);
            continue;
        }
        int n = snprintf(buf, sizeof(buf),
                         "{\"device_id\":\"c-temp-1\",\"timestamp_ms\":%ld,\"temperature_c\":%.1f}",
                         ts, val);
        if (n > 0) {
            sendto(sock, buf, (size_t)n, 0, (struct sockaddr*)&addr, sizeof(addr));
        }
        usleep(1000000);
    }
    close(sock);
    return 0;
}