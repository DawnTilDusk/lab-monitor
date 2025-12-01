#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <dirent.h>
#include <errno.h>

static int read_int_file(const char *path, int *out) {
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    char buf[64];
    if (!fgets(buf, sizeof(buf), f)) {
        fclose(f);
        return -1;
    }
    fclose(f);
    *out = atoi(buf);
    return 0;
}

static int path_exists(const char *p) {
    struct stat st;
    return stat(p, &st) == 0;
}

static int write_str(const char *path, const char *s) {
    int fd = open(path, O_WRONLY);
    if (fd < 0) return -1;
    size_t n = strlen(s);
    ssize_t w = write(fd, s, n);
    close(fd);
    return (w == (ssize_t)n) ? 0 : -1;
}

static int ensure_gpio_in(int n) {
    char dirp[128];
    char valp[128];
    char root[64];
    snprintf(root, sizeof(root), "/sys/class/gpio/gpio%d", n);
    if (!path_exists(root)) {
        char num[16];
        snprintf(num, sizeof(num), "%d", n);
        write_str("/sys/class/gpio/export", num);
    }
    snprintf(dirp, sizeof(dirp), "/sys/class/gpio/gpio%d/direction", n);
    write_str(dirp, "in");
    snprintf(valp, sizeof(valp), "/sys/class/gpio/gpio%d/value", n);
    return path_exists(valp) ? 0 : -1;
}

static int read_gpio_value(int n, int *out) {
    char p[128];
    snprintf(p, sizeof(p), "/sys/class/gpio/gpio%d/value", n);
    int fd = open(p, O_RDONLY);
    if (fd < 0) return -1;
    char b[8];
    ssize_t r = read(fd, b, sizeof(b)-1);
    close(fd);
    if (r <= 0) return -1;
    b[r] = '\0';
    int v = (b[0] == '1') ? 1 : 0;
    *out = v;
    return 0;
}

static int is_root() {
    return geteuid() == 0;
}

static const char* gpio_operate_path() {
    if (access("/usr/bin/gpio_operate", X_OK) == 0) return "/usr/bin/gpio_operate";
    if (access("/usr/sbin/gpio_operate", X_OK) == 0) return "/usr/sbin/gpio_operate";
    if (access("/bin/gpio_operate", X_OK) == 0) return "/bin/gpio_operate";
    return "gpio_operate";
}

static int has_gpio_operate() {
    const char *p = gpio_operate_path();
    return access(p, X_OK) == 0;
}

static int ensure_gpio_operate_in(int g, int p) {
    const char *bin = gpio_operate_path();
    char cmd[192];
    snprintf(cmd, sizeof(cmd), "%s set_direction %d %d 0", bin, g, p);
    if (!is_root()) {
        char scmd[224];
        snprintf(scmd, sizeof(scmd), "sudo -n %s", cmd);
        int rc = system(scmd);
        return rc == 0 ? 0 : -1;
    } else {
        int rc = system(cmd);
        return rc == 0 ? 0 : -1;
    }
}

static int read_gpio_operate_value(int g, int p, int *out) {
    const char *bin = gpio_operate_path();
    char base[192];
    snprintf(base, sizeof(base), "%s get_value %d %d", bin, g, p);
    char cmd[224];
    if (!is_root()) {
        snprintf(cmd, sizeof(cmd), "sudo -n %s", base);
    } else {
        snprintf(cmd, sizeof(cmd), "%s", base);
    }
    FILE *fp = popen(cmd, "r");
    if (!fp) return -1;
    char buf[256];
    size_t n = fread(buf, 1, sizeof(buf)-1, fp);
    pclose(fp);
    if (n == 0) return -1;
    buf[n] = '\0';
    char *pos = strstr(buf, "value is");
    if (!pos) return -1;
    pos += 9;
    while (*pos == ' ') pos++;
    int v = atoi(pos);
    *out = v ? 1 : 0;
    return 0;
}

static int read_light(int *out) {
    const char *grp = getenv("LIGHT_GPIO_GROUP");
    const char *pin = getenv("LIGHT_GPIO_PIN");
    if (grp && pin && strlen(grp) > 0 && strlen(pin) > 0) {
        int gg = atoi(grp);
        int pp = atoi(pin);
        static int inited = 0;
        if (!inited) {
            if (has_gpio_operate() && ensure_gpio_operate_in(gg, pp) == 0) inited = 1; else inited = -1;
        }
        if (inited == 1) {
            int v = 0;
            if (read_gpio_operate_value(gg, pp, &v) == 0) {
                const char *ah = getenv("LIGHT_GPIO_ACTIVE_HIGH");
                int active_high = (!ah || atoi(ah) != 0) ? 1 : 0;
                *out = active_high ? v : (v ? 0 : 1);
                return 0;
            }
        }
    }
    const char *gp = getenv("LIGHT_GPIO");
    if (gp && strlen(gp) > 0) {
        int g = atoi(gp);
        static int inited_sysfs = 0;
        if (!inited_sysfs) {
            if (ensure_gpio_in(g) == 0) inited_sysfs = 1; else inited_sysfs = -1;
        }
        if (inited_sysfs == 1) {
            int v = 0;
            if (read_gpio_value(g, &v) == 0) {
                const char *ah = getenv("LIGHT_GPIO_ACTIVE_HIGH");
                int active_high = (!ah || atoi(ah) != 0) ? 1 : 0;
                *out = active_high ? v : (v ? 0 : 1);
                return 0;
            }
        }
    }
    const char *p = getenv("LIGHT_SYSFS");
    if (p && strlen(p) > 0) {
        int v = 0;
        if (read_int_file(p, &v) == 0) {
            *out = v;
            return 0;
        }
    }
    return -1;
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
        int light = 0;
        if (read_light(&light) != 0) { usleep(1000000); continue; }
        int n = snprintf(buf, sizeof(buf),
                         "{\"device_id\":\"c-light-1\",\"timestamp_ms\":%ld,\"light\":%d}",
                         ts, light);
        if (n > 0) {
            sendto(sock, buf, (size_t)n, 0, (struct sockaddr*)&addr, sizeof(addr));
        }
        usleep(1000000);
    }
    close(sock);
    return 0;
}