#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <sys/time.h>
#include <math.h>
#include <linux/videodev2.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/stat.h>

static int capture_fswebcam(const char *dev, const char *filepath, int w, int h) {
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "fswebcam -q --no-banner -d %s -r %dx%d %s", dev, w, h, filepath);
    int rc = system(cmd);
    return (rc == 0) ? 0 : -1;
}

static int has_fswebcam();

static int open_cam(const char *dev) {
    int fd = open(dev, O_RDWR);
    return fd;
}

static int set_fmt(int fd, int w, int h) {
    struct v4l2_format fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    fmt.fmt.pix.width = w;
    fmt.fmt.pix.height = h;
    fmt.fmt.pix.pixelformat = V4L2_PIX_FMT_YUYV;
    fmt.fmt.pix.field = V4L2_FIELD_NONE;
    return ioctl(fd, VIDIOC_S_FMT, &fmt);
}

static int get_pixfmt(int fd) {
    struct v4l2_format fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(fd, VIDIOC_G_FMT, &fmt) == -1) return 0;
    return (int)fmt.fmt.pix.pixelformat;
}

struct buffer { void *start; size_t length; };

static int init_mmap(int fd, struct buffer *buf) {
    struct v4l2_requestbuffers req;
    memset(&req, 0, sizeof(req));
    req.count = 1;
    req.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    req.memory = V4L2_MEMORY_MMAP;
    if (ioctl(fd, VIDIOC_REQBUFS, &req) == -1) return -1;
    struct v4l2_buffer b;
    memset(&b, 0, sizeof(b));
    b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    b.memory = V4L2_MEMORY_MMAP;
    b.index = 0;
    if (ioctl(fd, VIDIOC_QUERYBUF, &b) == -1) return -1;
    buf->length = b.length;
    buf->start = mmap(NULL, b.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, b.m.offset);
    if (buf->start == MAP_FAILED) return -1;
    if (ioctl(fd, VIDIOC_QBUF, &b) == -1) return -1;
    enum v4l2_buf_type type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    if (ioctl(fd, VIDIOC_STREAMON, &type) == -1) return -1;
    return 0;
}

static int grab_frame(int fd, struct buffer *buf) {
    struct v4l2_buffer b;
    memset(&b, 0, sizeof(b));
    b.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    b.memory = V4L2_MEMORY_MMAP;
    if (ioctl(fd, VIDIOC_DQBUF, &b) == -1) return -1;
    int ok = (int)b.bytesused;
    if (ioctl(fd, VIDIOC_QBUF, &b) == -1) return -1;
    return ok;
}

static void yuyv_to_rgb(unsigned char *yuyv, int w, int h, unsigned char *rgb) {
    for (int i = 0, j = 0; i < w*h*2; i += 4) {
        int y0 = yuyv[i+0];
        int u  = yuyv[i+1];
        int y1 = yuyv[i+2];
        int v  = yuyv[i+3];
        int c = y0 - 16;
        int d = u - 128;
        int e = v - 128;
        int r = (298*c + 409*e + 128) >> 8;
        int g = (298*c - 100*d - 208*e + 128) >> 8;
        int b = (298*c + 516*d + 128) >> 8;
        rgb[j+0] = (unsigned char)(r<0?0:r>255?255:r);
        rgb[j+1] = (unsigned char)(g<0?0:g>255?255:g);
        rgb[j+2] = (unsigned char)(b<0?0:b>255?255:b);
        c = y1 - 16;
        r = (298*c + 409*e + 128) >> 8;
        g = (298*c - 100*d - 208*e + 128) >> 8;
        b = (298*c + 516*d + 128) >> 8;
        rgb[j+3] = (unsigned char)(r<0?0:r>255?255:r);
        rgb[j+4] = (unsigned char)(g<0?0:g>255?255:g);
        rgb[j+5] = (unsigned char)(b<0?0:b>255?255:b);
        j += 6;
    }
}

static void downsample_rgb(unsigned char *src, int sw, int sh, unsigned char *dst, int dw, int dh) {
    for (int y = 0; y < dh; y++) {
        int sy = y * sh / dh;
        for (int x = 0; x < dw; x++) {
            int sx = x * sw / dw;
            int si = (sy*sw + sx) * 3;
            int di = (y*dw + x) * 3;
            dst[di+0] = src[si+0];
            dst[di+1] = src[si+1];
            dst[di+2] = src[si+2];
        }
    }
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

    size_t cap = 200000;
    char *buf = (char*)malloc(cap);
    if (!buf) return 1;

    const char *dev = getenv("CAMERA_DEVICE");
    if (!dev) dev = "/dev/video0";
    const char *base = getenv("LAB_DIR");
    if (!base) base = "/home/openEuler/lab_monitor";
    char images_dir[256];
    snprintf(images_dir, sizeof(images_dir), "%s/static/images", base);
    mkdir(images_dir, 0755);
    int use_fs = has_fswebcam();
    int cam = -1; struct buffer mbuf; int sw = 160, sh = 120;
    unsigned char *rgb = NULL, *small = NULL;
    int fs_fail = 0;
    int fmt_rgb24 = 0;
    if (!use_fs) {
        cam = open_cam(dev);
        if (cam >= 0 && set_fmt(cam, sw, sh) != -1 && init_mmap(cam, &mbuf) != -1) {
            int pf = get_pixfmt(cam);
            if (pf != V4L2_PIX_FMT_YUYV) {
                struct v4l2_format fmt2; memset(&fmt2,0,sizeof(fmt2));
                fmt2.type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
                fmt2.fmt.pix.width = sw; fmt2.fmt.pix.height = sh;
                fmt2.fmt.pix.pixelformat = V4L2_PIX_FMT_RGB24;
                fmt2.fmt.pix.field = V4L2_FIELD_NONE;
                if (ioctl(cam, VIDIOC_S_FMT, &fmt2) != -1) {
                    int pf2 = get_pixfmt(cam);
                    if (pf2 == V4L2_PIX_FMT_RGB24) fmt_rgb24 = 1;
                }
            }
            rgb = (unsigned char*)malloc(sw*sh*3);
            small = (unsigned char*)malloc(32*24*3);
            fprintf(stderr, "[IMAGE] V4L2 fallback active %s %dx%d fmt=%s\n", dev, sw, sh, fmt_rgb24?"RGB24":"YUYV");
        } else {
            if (cam >= 0) close(cam);
            cam = -1;
            fprintf(stderr, "[IMAGE] V4L2 init failed %s\n", dev);
        }
    }
    for (;;) {
        long ts = now_ms();
        if (use_fs) {
            char filename[128];
            snprintf(filename, sizeof(filename), "relay_cam_%ld.jpg", ts);
            char filepath[384];
            snprintf(filepath, sizeof(filepath), "%s/%s", images_dir, filename);
            if (capture_fswebcam(dev, filepath, 640, 480) == 0) {
                size_t used = 0;
                int n = snprintf(buf + used, cap - used,
                                 "{\"device_id\":\"c-image-1\",\"timestamp_ms\":%ld,\"image_path\":\"/static/images/%s\"}",
                                 ts, filename);
                if (n > 0) sendto(sock, buf, (size_t)n, 0, (struct sockaddr*)&addr, sizeof(addr));
                fs_fail = 0;
            } else {
                fs_fail++;
                if (fs_fail >= 3) {
                    cam = open_cam(dev);
                    if (cam >= 0 && set_fmt(cam, sw, sh) != -1 && init_mmap(cam, &mbuf) != -1) {
                        rgb = (unsigned char*)malloc(sw*sh*3);
                        small = (unsigned char*)malloc(32*24*3);
                        use_fs = 0;
                        fprintf(stderr, "[IMAGE] switch to V4L2 after fswebcam failures %s\n", dev);
                    } else {
                        if (cam >= 0) close(cam);
                        cam = -1;
                        fprintf(stderr, "[IMAGE] V4L2 init failed %s\n", dev);
                    }
                }
            }
        } else if (cam >= 0 && rgb && small) {
            int got = grab_frame(cam, &mbuf);
            if (got > 0) {
                fprintf(stderr, "[IMAGE] frame bytes=%d\n", got);
                if (fmt_rgb24) {
                    int need = sw*sh*3;
                    if (got >= need) memcpy(rgb, mbuf.start, (size_t)need);
                    else continue;
                } else {
                    if (got >= sw*sh*2) yuyv_to_rgb((unsigned char*)mbuf.start, sw, sh, rgb);
                    else continue;
                }
                downsample_rgb(rgb, sw, sh, small, 32, 24);
                size_t used = 0;
                int n = snprintf(buf + used, cap - used,
                                 "{\"device_id\":\"c-image-1\",\"timestamp_ms\":%ld,\"frame\":{\"width\":%d,\"height\":%d,\"pixels\":[",
                                 ts, 32, 24);
                if (n <= 0) break; used += (size_t)n;
                for (int y = 0; y < 24; y++) {
                    n = snprintf(buf + used, cap - used, y==0?"[":",[" ); if (n <= 0) break; used += (size_t)n;
                    for (int x = 0; x < 32; x++) {
                        int di = (y*32 + x) * 3; int r = small[di+0]; int g = small[di+1]; int bb = small[di+2];
                        n = snprintf(buf + used, cap - used, x==0?"{\"r\":%d,\"g\":%d,\"b\":%d}":",{\"r\":%d,\"g\":%d,\"b\":%d}", r,g,bb);
                        if (n <= 0) break; used += (size_t)n;
                    }
                    n = snprintf(buf + used, cap - used, "]"); if (n <= 0) break; used += (size_t)n;
                }
                n = snprintf(buf + used, cap - used, "]}}" ); if (n <= 0) break; used += (size_t)n;
                sendto(sock, buf, used, 0, (struct sockaddr*)&addr, sizeof(addr));
            }
        }
        usleep(10000000);
    }
    if (rgb) free(rgb);
    if (small) free(small);
    if (cam >= 0) { munmap(mbuf.start, mbuf.length); close(cam); }
    free(buf);
    close(sock);
    return 0;
}

static int has_fswebcam() {
    int rc = system("command -v fswebcam >/dev/null 2>&1");
    return rc == 0;
}