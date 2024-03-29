#include <freertos/FreeRTOS.h>
#include <esp_adc_cal.h>
#include <driver/gpio.h>
#include <driver/adc.h>
#include <driver/i2c.h>
#include <string.h>

#include "rg_system.h"
#include "rg_input.h"

static bool input_initialized = false;
static bool use_external_gamepad = false;
static int64_t last_gamepad_read = 0;
static gamepad_state_t gamepad_state;


static inline uint32_t console_gamepad_read(void)
{
    uint32_t state = 0;

    int joyX = adc1_get_raw(RG_GPIO_GAMEPAD_X);
    int joyY = adc1_get_raw(RG_GPIO_GAMEPAD_Y);

    if (joyY > 2048 + 1024) state |= GAMEPAD_KEY_UP;
    else if (joyY > 1024)   state |= GAMEPAD_KEY_DOWN;
    if (joyX > 2048 + 1024) state |= GAMEPAD_KEY_LEFT;
    else if (joyX > 1024)   state |= GAMEPAD_KEY_RIGHT;

    if (!gpio_get_level(RG_GPIO_GAMEPAD_MENU))   state |= GAMEPAD_KEY_MENU;
    if (!gpio_get_level(RG_GPIO_GAMEPAD_VOLUME)) state |= GAMEPAD_KEY_VOLUME;
    if (!gpio_get_level(RG_GPIO_GAMEPAD_SELECT)) state |= GAMEPAD_KEY_SELECT;
    if (!gpio_get_level(RG_GPIO_GAMEPAD_START))  state |= GAMEPAD_KEY_START;
    if (!gpio_get_level(RG_GPIO_GAMEPAD_A))      state |= GAMEPAD_KEY_A;
    if (!gpio_get_level(RG_GPIO_GAMEPAD_B))      state |= GAMEPAD_KEY_B;

    return state;
}

static inline uint32_t external_gamepad_read(void)
{
    uint32_t state = 0;

    // Unfortunately the GO doesn't bring out enough GPIO for both ext DAC and controller...
    if (rg_audio_get_sink() != RG_AUDIO_SINK_EXT_DAC)
    {
        // NES / SNES shift register
    }

    return state;
}

static void input_task(void *arg)
{
    uint8_t debounce[GAMEPAD_KEY_COUNT];
    const uint8_t debounce_level = 0x03;
    gamepad_state_t input_state = 0;

    // Initialize debounce state
    memset(debounce, 0xFF, sizeof(debounce));

    while (input_initialized)
    {
        uint32_t state = console_gamepad_read();

        if (use_external_gamepad)
        {
            state |= external_gamepad_read();
        }

        for (int i = 0; i < GAMEPAD_KEY_COUNT; ++i)
        {
            debounce[i] = ((debounce[i] << 1) | ((state >> i) & 1));
            debounce[i] &= debounce_level;

            if (debounce[i] == debounce_level) // Pressed
            {
                input_state |= (1 << i);
            }
            else if (debounce[i] == 0x00) // Released
            {
                input_state &= ~(1 << i);
            }
        }

        gamepad_state = input_state;

        vTaskDelay(pdMS_TO_TICKS(10));
    }

    vTaskDelete(NULL);
}

void rg_input_init(void)
{
    if (input_initialized)
    {
        RG_LOGE("Input already initialized...\n");
        return;
    }

    adc1_config_width(ADC_WIDTH_12Bit);
    adc1_config_channel_atten(RG_GPIO_GAMEPAD_X, ADC_ATTEN_11db);
    adc1_config_channel_atten(RG_GPIO_GAMEPAD_Y, ADC_ATTEN_11db);

    gpio_set_direction(RG_GPIO_GAMEPAD_MENU, GPIO_MODE_INPUT);
    gpio_set_pull_mode(RG_GPIO_GAMEPAD_MENU, GPIO_PULLUP_ONLY);
    gpio_set_direction(RG_GPIO_GAMEPAD_VOLUME, GPIO_MODE_INPUT);
    // gpio_set_pull_mode(RG_GPIO_GAMEPAD_VOLUME, GPIO_PULLUP_ONLY);

    gpio_set_direction(RG_GPIO_GAMEPAD_SELECT, GPIO_MODE_INPUT);
    gpio_set_pull_mode(RG_GPIO_GAMEPAD_SELECT, GPIO_PULLUP_ONLY);
    gpio_set_direction(RG_GPIO_GAMEPAD_START, GPIO_MODE_INPUT);
    // gpio_set_direction(RG_GPIO_GAMEPAD_START, GPIO_PULLUP_ONLY);

    gpio_set_direction(RG_GPIO_GAMEPAD_A, GPIO_MODE_INPUT);
    gpio_set_pull_mode(RG_GPIO_GAMEPAD_A, GPIO_PULLUP_ONLY);
    gpio_set_direction(RG_GPIO_GAMEPAD_B, GPIO_MODE_INPUT);
    gpio_set_pull_mode(RG_GPIO_GAMEPAD_B, GPIO_PULLUP_ONLY);

    // Start background polling
    xTaskCreatePinnedToCore(&input_task, "input_task", 1024, NULL, 5, NULL, 1);

    input_initialized = true;

      RG_LOGI("init done.\n");
}

void rg_input_deinit(void)
{
    input_initialized = false;
}

long rg_input_gamepad_last_read(void)
{
    if (!last_gamepad_read)
        return 0;

    return get_elapsed_time_since(last_gamepad_read);
}

gamepad_state_t rg_input_read_gamepad(void)
{
    last_gamepad_read = get_elapsed_time();
    return gamepad_state;
}

bool rg_input_key_is_pressed(gamepad_key_t key)
{
    return (rg_input_read_gamepad() & key) ? true : false;
}

void rg_input_wait_for_key(gamepad_key_t key, bool pressed)
{
    while (rg_input_key_is_pressed(key) != pressed)
    {
        vTaskDelay(1);
    }
}

battery_state_t rg_input_read_battery()
{
    static esp_adc_cal_characteristics_t adc_chars;
    static float adcValue = 0.0f;

    const int sampleCount = 4;
    float adcSample = 0.0f;

    // ADC not initialized
    if (adc_chars.vref == 0)
    {
        adc1_config_width(ADC_WIDTH_12Bit);
        adc1_config_channel_atten(ADC1_CHANNEL_0, ADC_ATTEN_11db);
        esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_11db, ADC_WIDTH_BIT_12, 1100, &adc_chars);
    }

    for (int i = 0; i < sampleCount; ++i)
    {
        adcSample += esp_adc_cal_raw_to_voltage(adc1_get_raw(ADC1_CHANNEL_0), &adc_chars) * 0.001f;
    }
    adcSample /= sampleCount;

    if (adcValue == 0.0f)
    {
        adcValue = adcSample;
    }
    else
    {
        adcValue += adcSample;
        adcValue /= 2.0f;
    }

    const float Vs = (adcValue / RG_BATT_DIVIDER_R2 * (RG_BATT_DIVIDER_R1 + RG_BATT_DIVIDER_R2));
    const float Vconst = RG_MAX(RG_BATT_VOLTAGE_EMPTY, RG_MIN(Vs, RG_BATT_VOLTAGE_FULL));

    battery_state_t out_state = {
        .millivolts = (int)(Vs * 1000),
        .percentage = (int)((Vconst - RG_BATT_VOLTAGE_EMPTY) / (RG_BATT_VOLTAGE_FULL - RG_BATT_VOLTAGE_EMPTY) * 100.0f),
    };

    return out_state;
}
