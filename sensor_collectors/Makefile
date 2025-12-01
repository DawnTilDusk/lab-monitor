CC ?= gcc
CFLAGS ?= -O2 -Wall

BIN_TEMP = sensor_temp_collector
BIN_LIGHT = sensor_light_collector
BIN_IMAGE = sensor_image_collector

all: $(BIN_TEMP) $(BIN_LIGHT) $(BIN_IMAGE)

$(BIN_TEMP): sensor_temp_collector.c
	$(CC) $(CFLAGS) $< -o $@

$(BIN_LIGHT): sensor_light_collector.c
	$(CC) $(CFLAGS) $< -o $@

$(BIN_IMAGE): sensor_image_collector.c
	$(CC) $(CFLAGS) $< -o $@ -lm

clean:
	rm -f $(BIN_TEMP) $(BIN_LIGHT) $(BIN_IMAGE)
