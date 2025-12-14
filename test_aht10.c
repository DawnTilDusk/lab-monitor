#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <linux/i2c-dev.h>
#include <errno.h>

// AHT10 Default Address
#define AHT10_ADDR 0x38
// Device Path (Using I2C-7 based on your pin configuration)
#define I2C_DEV_PATH "/dev/i2c-7"

int main() {
    int file;
    char filename[20];
    unsigned char cmd[3];
    unsigned char data[6];

    printf("Starting AHT10 Sensor Test...\n");
    printf("Target I2C Bus: %s\n", I2C_DEV_PATH);
    printf("Device Address: 0x%02X\n", AHT10_ADDR);

    // 1. Open I2C Bus
    snprintf(filename, 19, "%s", I2C_DEV_PATH);
    if ((file = open(filename, O_RDWR)) < 0) {
        perror("Failed to open the bus");
        printf("Check if I2C drivers are loaded and you have permission.\n");
        return 1;
    }

    // 2. Connect to Device
    if (ioctl(file, I2C_SLAVE, AHT10_ADDR) < 0) {
        perror("Failed to acquire bus access and/or talk to slave");
        close(file);
        return 1;
    }

    // 3. Send Initialization Command (0xE1 0x08 0x00)
    // Note: Some datasheets suggest 0xE1 0x08 0x00 for calibration
    cmd[0] = 0xE1;
    cmd[1] = 0x08;
    cmd[2] = 0x00;
    if (write(file, cmd, 3) != 3) {
        perror("Failed to send init command");
        // Don't exit here, maybe it's already running
    }
    usleep(50000); // Wait 50ms

    // 4. Measure Loop (Run 5 times)
    for (int i = 0; i < 5; i++) {
        printf("\n--- Reading #%d ---\n", i + 1);

        // Trigger Measurement (0xAC 0x33 0x00)
        cmd[0] = 0xAC;
        cmd[1] = 0x33;
        cmd[2] = 0x00;
        if (write(file, cmd, 3) != 3) {
            perror("Failed to trigger measurement");
            continue;
        }

        // Wait for measurement (>75ms according to datasheet)
        usleep(80000);

        // Read 6 bytes of data
        if (read(file, data, 6) != 6) {
            perror("Failed to read data");
            continue;
        }

        // Print Raw Bytes for Debugging
        printf("Raw Data: %02X %02X %02X %02X %02X %02X\n", 
               data[0], data[1], data[2], data[3], data[4], data[5]);

        // Check Status Byte (Byte 0)
        // Bit 7: Busy (1=Busy)
        // Bit 3: Calibrated (1=Calibrated)
        if ((data[0] & 0x80) != 0) {
            printf("Warning: Device is busy\n");
        }
        if ((data[0] & 0x08) == 0) {
            printf("Warning: Device not calibrated\n");
        }

        // Calculate Humidity
        // Humidity is 20 bits: Byte1, Byte2, Byte3[7:4]
        unsigned long raw_hum = ((unsigned long)data[1] << 12) | 
                                ((unsigned long)data[2] << 4) | 
                                ((unsigned long)(data[3] & 0xF0) >> 4);
        double humidity = ((double)raw_hum * 100) / 1048576;

        // Calculate Temperature
        // Temperature is 20 bits: Byte3[3:0], Byte4, Byte5
        unsigned long raw_temp = ((unsigned long)(data[3] & 0x0F) << 16) | 
                                 ((unsigned long)data[4] << 8) | 
                                 (unsigned long)data[5];
        double temp = ((double)raw_temp * 200) / 1048576 - 50;

        printf("Temperature: %.2f C\n", temp);
        printf("Humidity:    %.2f %%\n", humidity);

        sleep(1);
    }

    close(file);
    printf("\nTest Completed.\n");
    return 0;
}
