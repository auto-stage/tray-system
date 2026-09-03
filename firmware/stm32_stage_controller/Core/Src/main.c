/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum
{
  STAGE_AXIS_X = 0,
  STAGE_AXIS_Z,
  STAGE_AXIS_G,
  STAGE_AXIS_COUNT
} StageAxisId;

typedef enum
{
  STAGE_MODE_IDLE = 0,
  STAGE_MODE_MOVE,
  STAGE_MODE_JOG,
  STAGE_MODE_HOME_FAST,
  STAGE_MODE_HOME_WAIT_BACKOFF,
  STAGE_MODE_HOME_BACKOFF,
  STAGE_MODE_HOME_WAIT_SLOW,
  STAGE_MODE_HOME_SLOW,
  STAGE_MODE_HOME_WAIT_FINAL_BACKOFF,
  STAGE_MODE_HOME_FINAL_BACKOFF,
  STAGE_MODE_SOFT_STOP,
  STAGE_MODE_FAULT
} StageMode;

typedef enum
{
  STAGE_OK = 0,
  STAGE_ERR_AXIS,
  STAGE_ERR_BUSY,
  STAGE_ERR_DISABLED,
  STAGE_ERR_ESTOP,
  STAGE_ERR_LIMIT,
  STAGE_ERR_SOFT_LIMIT,
  STAGE_ERR_PARAM
} StageResult;

typedef struct
{
  StageMode mode;
  int64_t position_steps;
  uint64_t remaining_steps;
  uint32_t current_hz;
  uint32_t target_hz;
  float steps_per_mm;
  float position_mm;
  bool enabled;
  bool homed;
  bool min_limit;
  bool max_limit;
} StageAxisStatus;

typedef struct
{
  TIM_HandleTypeDef *htim;
  uint32_t channel;
  GPIO_TypeDef *dir_port;
  uint16_t dir_pin;
  GPIO_TypeDef *ena_port;
  uint16_t ena_pin;
  GPIO_PinState dir_positive_level;
  GPIO_PinState enabled_level;
  GPIO_TypeDef *min_port;
  uint16_t min_pin;
  GPIO_TypeDef *max_port;
  uint16_t max_pin;
  volatile StageMode mode;
  volatile int64_t position_steps;
  volatile uint64_t remaining_steps;
  volatile uint32_t current_hz;
  volatile uint32_t target_hz;
  uint32_t start_hz;
  float accel_steps_s2;
  float steps_per_mm;
  float soft_min_mm;
  float soft_max_mm;
  int64_t soft_min_steps;
  int64_t soft_max_steps;
  bool positive;
  bool enabled;
  bool homed;
  volatile uint8_t min_limit_count;
  volatile uint8_t max_limit_count;
} StageAxis;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
/* --------------------------------------------------------------------------
 * 사용자 조정 상수
 * -------------------------------------------------------------------------- */

/* 센서가 실제 배선되지 않은 벤치 시험 때만 0으로 바꾸십시오.
 * 0이면 물리 안전입력을 무시하므로 실기구 운전에는 사용하면 안 됩니다. */
#define STAGE_USE_LIMIT_INPUTS             1U
#define STAGE_USE_ESTOP_INPUT              0U

/* PUL: X=PE9/TIM1_CH1, Z=PC6/TIM8_CH1 (CubeMX 설정 유지) */
#define STAGE_X_STEP_CHANNEL               TIM_CHANNEL_1
#define STAGE_Z_STEP_CHANNEL               TIM_CHANNEL_1

/* 현재 main.c에 이미 잡혀 있는 PD4\~PD7을 그대로 사용합니다. */
#define STAGE_X_DIR_GPIO_PORT              GPIOD
#define STAGE_X_DIR_GPIO_PIN               GPIO_PIN_4
#define STAGE_X_ENA_GPIO_PORT              GPIOD
#define STAGE_X_ENA_GPIO_PIN               GPIO_PIN_5
#define STAGE_Z_DIR_GPIO_PORT              GPIOD
#define STAGE_Z_DIR_GPIO_PIN               GPIO_PIN_6
#define STAGE_Z_ENA_GPIO_PORT              GPIOD
#define STAGE_Z_ENA_GPIO_PIN               GPIO_PIN_7

/* NC 접점: 정상=GND/LOW, 작동 또는 단선=Pull-up/HIGH */
#define STAGE_X_MIN_GPIO_PORT              GPIOF
#define STAGE_X_MIN_GPIO_PIN               GPIO_PIN_12
#define STAGE_X_MAX_GPIO_PORT              GPIOF
#define STAGE_X_MAX_GPIO_PIN               GPIO_PIN_13
#define STAGE_Z_MIN_GPIO_PORT              GPIOF
#define STAGE_Z_MIN_GPIO_PIN               GPIO_PIN_14
#define STAGE_Z_MAX_GPIO_PORT              GPIOF
#define STAGE_Z_MAX_GPIO_PIN               GPIO_PIN_15
#define STAGE_ESTOP_GPIO_PORT              GPIOG
#define STAGE_ESTOP_GPIO_PIN               GPIO_PIN_2
#define STAGE_LIMIT_ACTIVE_LEVEL           GPIO_PIN_SET
#define STAGE_ESTOP_ACTIVE_LEVEL           GPIO_PIN_SET

/* 실제 회전 방향/ENA 진리표가 반대이면 이 4개 상수만 반전합니다. */
#define STAGE_X_DIR_POSITIVE_LEVEL         GPIO_PIN_SET
#define STAGE_Z_DIR_POSITIVE_LEVEL         GPIO_PIN_RESET
#define STAGE_X_DRIVER_ENABLED_LEVEL       GPIO_PIN_RESET
#define STAGE_Z_DRIVER_ENABLED_LEVEL       GPIO_PIN_RESET

/* 현재 HSI/APB2=16 MHz, TIM1/TIM8 PSC=15 -> 타이머 tick=1 MHz */
#define STAGE_TIMER_TICK_HZ                1000000UL
#define STAGE_MIN_STEP_HZ                  20UL
#define STAGE_MAX_STEP_HZ                  50000UL
#define STAGE_DEFAULT_START_HZ             100UL

/* 기구 사양 확정 후 수정할 값 */
#define STAGE_X_DEFAULT_STEPS_PER_MM       320.0f
#define STAGE_Z_DEFAULT_STEPS_PER_MM       320.0f
#define STAGE_X_DEFAULT_MIN_MM             0.0f
#define STAGE_X_DEFAULT_MAX_MM             1000.0f
#define STAGE_Z_DEFAULT_MIN_MM             0.0f
#define STAGE_Z_DEFAULT_MAX_MM             700.0f
#define STAGE_HOME_FAST_MM_S               5.0f
#define STAGE_HOME_SLOW_MM_S               1.0f
#define STAGE_HOME_ACCEL_MM_S2             20.0f
#define STAGE_HOME_BACKOFF_MM              3.0f

/* HX711 #1 (캐리지 로드셀)
 * DOUT/DT = PG0 (input), SCK/CLK = PG1 (output)
 * Channel A, Gain 128 사용 */
#define HX711_1_DOUT_GPIO_PORT             GPIOG
#define HX711_1_DOUT_GPIO_PIN              GPIO_PIN_0
#define HX711_1_SCK_GPIO_PORT              GPIOG
#define HX711_1_SCK_GPIO_PIN               GPIO_PIN_1
#define HX711_1_READY_TIMEOUT_MS            200U

/* HX711 #1 calibration */
#define HX711_1_TARE_RAW                    60834.7f
#define HX711_1_COUNT_PER_G                 668.7673f

/* HX711 #2 (최종 검수 박스 로드셀)
 * DOUT/DT = PG3 (input), SCK/CLK = PG4 (output)
 * Channel A, Gain 128 사용
 *
 * TARE와 calibration 값은 현재 runtime에서 설정한다.
 * STM32 재부팅 후에는 다시 TARE/CAL이 필요하다.
 */
#define HX711_2_DOUT_GPIO_PORT              GPIOG
#define HX711_2_DOUT_GPIO_PIN               GPIO_PIN_3
#define HX711_2_SCK_GPIO_PORT               GPIOF
#define HX711_2_SCK_GPIO_PIN                GPIO_PIN_7
#define HX711_2_READY_TIMEOUT_MS            200U
#define HX711_2_DEFAULT_SAMPLES             10U

static float HX711_2_TareRaw = 0.0f;
static float HX711_2_CountPerG = 0.0f;
static bool HX711_2_TareValid = false;
static bool HX711_2_CalValid = false;


/* MG996R gripper servo - PA0 / TIM2_CH1 */
#define SERVO_GPIO_PORT                     GPIOA
#define SERVO_GPIO_PIN                      GPIO_PIN_0
#define SERVO_GPIO_AF                       GPIO_AF1_TIM2

#define SERVO_MIN_PULSE_US                  500U
#define SERVO_CENTER_PULSE_US               1500U
#define SERVO_MAX_PULSE_US                  2500U

/* Gripper servo temporary angles.
 * 실제 그리퍼 장착 후 OPEN/CLOSE 각도만 재조정한다. */
#define GRIP_OPEN_ANGLE_DEG                 30U
#define GRIP_CLOSE_ANGLE_DEG                150U

/* GRIPPER_STEPPER_FINAL_V1
 * 검증 완료된 rack stepper 설정
 * STEP = PD14 / TIM4_CH3 (D10), DIR = PD15 (D9)
 * RETRACT limit = PE11 (D5), EXTEND limit = PE13 (D3)
 * NC contact + internal pull-up: normal LOW, active/disconnected HIGH.
 *
 * HOME: RETRACT limit -> 3 mm release -> slow re-touch -> 3 mm final release.
 * The final released point is logical 0 mm.
 * Normal motion uses absolute targets: RETRACT=0 mm, EXTEND=200 mm.
 */
#define GRIPPER_STEP_CHANNEL                 TIM_CHANNEL_3
#define GRIPPER_DIR_GPIO_PORT                GPIOD
#define GRIPPER_DIR_GPIO_PIN                 GPIO_PIN_15
#define GRIPPER_DIR_EXTEND_LEVEL             GPIO_PIN_SET
#define GRIPPER_RETRACT_LIMIT_GPIO_PORT      GPIOE
#define GRIPPER_RETRACT_LIMIT_GPIO_PIN       GPIO_PIN_11
#define GRIPPER_EXTEND_LIMIT_GPIO_PORT       GPIOE
#define GRIPPER_EXTEND_LIMIT_GPIO_PIN        GPIO_PIN_13
#define GRIPPER_DEFAULT_STEPS_PER_MM         10.616f
#define GRIPPER_EXTEND_POSITION_MM           200.0f
#define GRIPPER_RETRACT_POSITION_MM          0.0f
#define GRIPPER_DEFAULT_STEP_HZ              800U
#define GRIPPER_HOME_STEP_HZ                 400U
#define GRIPPER_DEFAULT_SPEED_MM_S           ((float)GRIPPER_DEFAULT_STEP_HZ / GRIPPER_DEFAULT_STEPS_PER_MM)
#define GRIPPER_DEFAULT_ACCEL_MM_S2          200.0f
#define GRIPPER_HOME_ACCEL_STEPS_S2          1200.0f
#define GRIPPER_HOME_BACKOFF_MM              3.0f
#define GRIPPER_SOFT_MIN_MM                  0.0f
#define GRIPPER_SOFT_MAX_MM                  GRIPPER_EXTEND_POSITION_MM
#define GRIPPER_LIMIT_CONFIRM_PULSES         3U

#define STAGE_CONTROL_PERIOD_MS            10U
#define STAGE_RX_RING_SIZE                 512U
#define STAGE_LINE_SIZE                    192U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim2;
TIM_HandleTypeDef htim4;
TIM_HandleTypeDef htim8;

UART_HandleTypeDef huart3;

/* USER CODE BEGIN PV */
static uint32_t stage_last_10ms = 0U;
static StageAxis stage_axis[STAGE_AXIS_COUNT];
static volatile bool stage_estop_latched = false;

static UART_HandleTypeDef *stage_uart = NULL;
static uint8_t stage_rx_byte = 0U;
static volatile uint16_t stage_rx_head = 0U;
static volatile uint16_t stage_rx_tail = 0U;
static volatile char stage_rx_ring[STAGE_RX_RING_SIZE];
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MPU_Config(void);
static void MX_GPIO_Init(void);
static void MX_TIM1_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_TIM8_Init(void);
/* USER CODE BEGIN PFP */
static void Stage_Init(TIM_HandleTypeDef *htim_x, TIM_HandleTypeDef *htim_z,
                       TIM_HandleTypeDef *htim_g);
static void Stage_Process10ms(void);
static void Stage_OnTimerPeriodElapsed(TIM_HandleTypeDef *htim);
static StageResult Stage_Enable(StageAxisId id, bool enable);
static StageResult Stage_MoveMm(StageAxisId id, float distance_mm,
                                float max_speed_mm_s, float accel_mm_s2);
static StageResult Stage_JogMmS(StageAxisId id, float signed_speed_mm_s,
                               float accel_mm_s2);
static StageResult Stage_Home(StageAxisId id);
static StageResult Stage_Stop(StageAxisId id, bool hard);
static void Stage_StopAll(bool hard);
static void Stage_EStop(void);
static StageResult Stage_ResetEStop(void);
static StageResult Stage_Zero(StageAxisId id);
static StageResult Stage_SetStepsPerMm(StageAxisId id, float steps_per_mm);
static StageResult Stage_SetSoftLimitsMm(StageAxisId id, float min_mm, float max_mm);
static void Stage_GetStatus(StageAxisId id, StageAxisStatus *out);
static const char *Stage_ModeName(StageMode mode);
static const char *Stage_ResultName(StageResult result);

static void StageProtocol_Init(UART_HandleTypeDef *huart);
static void StageProtocol_Process(void);
static void StageProtocol_OnRxComplete(UART_HandleTypeDef *huart);
static void StageProtocol_SendStatus(void);
static void StageProtocol_SendGripperStatus(void);
static void GripperStepper_TimerInit(void);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static GPIO_PinState Stage_OppositeLevel(GPIO_PinState state)
{
  return (state == GPIO_PIN_SET) ? GPIO_PIN_RESET : GPIO_PIN_SET;
}

static bool Stage_InputActive(GPIO_TypeDef *port, uint16_t pin,
                              GPIO_PinState active_level)
{
  return (HAL_GPIO_ReadPin(port, pin) == active_level);
}

/* --------------------------------------------------------------------------
 * HX711 #1 - 캐리지 로드셀
 * -------------------------------------------------------------------------- */

/* HX711은 변환 데이터가 준비되면 DOUT을 LOW로 내립니다. */
static bool HX711_1_IsReady(void)
{
  return (HAL_GPIO_ReadPin(HX711_1_DOUT_GPIO_PORT,
                           HX711_1_DOUT_GPIO_PIN) == GPIO_PIN_RESET);
}

/* Channel A / Gain 128 기준 24-bit signed RAW 값을 읽습니다.
 * 반환값 INT32_MIN은 DOUT ready timeout을 의미합니다.
 *
 * SCK가 HIGH인 채로 오래 유지되면 HX711 power-down 조건이 될 수 있으므로,
 * 각 clock의 SCK HIGH 구간만 짧게 IRQ를 막아 HIGH 시간을 보장합니다.
 */
static int32_t HX711_1_ReadRaw(void)
{
  uint32_t value = 0U;
  uint32_t start_ms = HAL_GetTick();
  uint32_t primask;
  uint32_t i;

  while (!HX711_1_IsReady())
  {
    if ((uint32_t)(HAL_GetTick() - start_ms) >= HX711_1_READY_TIMEOUT_MS)
    {
      return INT32_MIN;
    }
  }

  for (i = 0U; i < 24U; ++i)
  {
    /* SCK HIGH 구간만 아주 짧게 보호합니다.
     * STEP/UART IRQ는 SCK LOW 동안 계속 처리될 수 있습니다. */
    primask = __get_PRIMASK();
    __disable_irq();

    HAL_GPIO_WritePin(HX711_1_SCK_GPIO_PORT,
                      HX711_1_SCK_GPIO_PIN,
                      GPIO_PIN_SET);

    value <<= 1U;
    if (HAL_GPIO_ReadPin(HX711_1_DOUT_GPIO_PORT,
                         HX711_1_DOUT_GPIO_PIN) == GPIO_PIN_SET)
    {
      value |= 1U;
    }

    HAL_GPIO_WritePin(HX711_1_SCK_GPIO_PORT,
                      HX711_1_SCK_GPIO_PIN,
                      GPIO_PIN_RESET);

    if (primask == 0U)
    {
      __enable_irq();
    }
  }

  /* 25번째 pulse: 다음 변환을 Channel A / Gain 128로 설정 */
  primask = __get_PRIMASK();
  __disable_irq();

  HAL_GPIO_WritePin(HX711_1_SCK_GPIO_PORT,
                    HX711_1_SCK_GPIO_PIN,
                    GPIO_PIN_SET);
  HAL_GPIO_WritePin(HX711_1_SCK_GPIO_PORT,
                    HX711_1_SCK_GPIO_PIN,
                    GPIO_PIN_RESET);

  if (primask == 0U)
  {
    __enable_irq();
  }

  /* HX711 24-bit two's-complement -> int32_t sign extension */
  if ((value & 0x00800000U) != 0U)
  {
    value |= 0xFF000000U;
  }

  return (int32_t)value;
}


/* --------------------------------------------------------------------------
 * HX711 #2 - 최종 검수 박스 로드셀
 * -------------------------------------------------------------------------- */

static bool HX711_2_IsReady(void)
{
  return (HAL_GPIO_ReadPin(HX711_2_DOUT_GPIO_PORT,
                           HX711_2_DOUT_GPIO_PIN) == GPIO_PIN_RESET);
}


static int32_t HX711_2_ReadRaw(void)
{
  uint32_t value = 0U;
  uint32_t start_ms = HAL_GetTick();
  uint32_t primask;
  uint32_t i;

  while (!HX711_2_IsReady())
  {
    if ((uint32_t)(HAL_GetTick() - start_ms) >= HX711_2_READY_TIMEOUT_MS)
    {
      return INT32_MIN;
    }
  }

  for (i = 0U; i < 24U; ++i)
  {
    primask = __get_PRIMASK();
    __disable_irq();

    HAL_GPIO_WritePin(HX711_2_SCK_GPIO_PORT,
                      HX711_2_SCK_GPIO_PIN,
                      GPIO_PIN_SET);

    value <<= 1U;

    if (HAL_GPIO_ReadPin(HX711_2_DOUT_GPIO_PORT,
                         HX711_2_DOUT_GPIO_PIN) == GPIO_PIN_SET)
    {
      value |= 1U;
    }

    HAL_GPIO_WritePin(HX711_2_SCK_GPIO_PORT,
                      HX711_2_SCK_GPIO_PIN,
                      GPIO_PIN_RESET);

    if (primask == 0U)
    {
      __enable_irq();
    }
  }

  /* 25번째 pulse: 다음 변환도 Channel A / Gain 128 */
  primask = __get_PRIMASK();
  __disable_irq();

  HAL_GPIO_WritePin(HX711_2_SCK_GPIO_PORT,
                    HX711_2_SCK_GPIO_PIN,
                    GPIO_PIN_SET);

  HAL_GPIO_WritePin(HX711_2_SCK_GPIO_PORT,
                    HX711_2_SCK_GPIO_PIN,
                    GPIO_PIN_RESET);

  if (primask == 0U)
  {
    __enable_irq();
  }

  if ((value & 0x00800000U) != 0U)
  {
    value |= 0xFF000000U;
  }

  return (int32_t)value;
}


/* 여러 변환값 평균.
 * false 반환은 중간에 HX711 ready timeout이 발생한 경우입니다. */
static bool HX711_2_ReadAverage(uint32_t samples, float *average_raw)
{
  int64_t sum = 0;
  uint32_t i;

  if ((samples == 0U) || (average_raw == NULL))
  {
    return false;
  }

  for (i = 0U; i < samples; ++i)
  {
    int32_t raw = HX711_2_ReadRaw();

    if (raw == INT32_MIN)
    {
      return false;
    }

    sum += (int64_t)raw;
  }

  *average_raw =
      (float)sum / (float)samples;

  return true;
}


/* --------------------------------------------------------------------------
 * MG996R gripper servo - PA0 / TIM2_CH1
 * 50 Hz PWM, 1000~2000 us
 * -------------------------------------------------------------------------- */

static bool Servo_Initialized = false;

static void Servo_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};

  uint32_t pclk1;
  uint32_t tim_clk;
  uint32_t prescaler;

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_TIM2_CLK_ENABLE();

  GPIO_InitStruct.Pin = SERVO_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.Alternate = SERVO_GPIO_AF;
  HAL_GPIO_Init(SERVO_GPIO_PORT, &GPIO_InitStruct);

  pclk1 = HAL_RCC_GetPCLK1Freq();

  if ((RCC->CFGR & RCC_CFGR_PPRE1) == RCC_HCLK_DIV1)
  {
    tim_clk = pclk1;
  }
  else
  {
    tim_clk = pclk1 * 2U;
  }

  /* Timer counter = 1 MHz -> 1 count = 1 us */
  prescaler = (tim_clk / 1000000U) - 1U;

  htim2.Instance = TIM2;
  htim2.Init.Prescaler = prescaler;
  htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim2.Init.Period = 20000U - 1U;      /* 20 ms = 50 Hz */
  htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;

  if (HAL_TIM_PWM_Init(&htim2) != HAL_OK)
  {
    Error_Handler();
  }

  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = SERVO_CENTER_PULSE_US;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;

  if (HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }

  if (HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }

  Servo_Initialized = true;
}

static void Servo_SetAngle(uint32_t angle_deg)
{
  uint32_t pulse_us;

  if (!Servo_Initialized)
  {
    Servo_Init();
  }

  if (angle_deg > 180U)
  {
    angle_deg = 180U;
  }

  pulse_us =
      SERVO_MIN_PULSE_US +
      ((SERVO_MAX_PULSE_US - SERVO_MIN_PULSE_US) * angle_deg) / 180U;

  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, pulse_us);
}

/* --------------------------------------------------------------------------
 * Gripper rack stepper - PD14/TIM4_CH3 + PD15 DIR
 * -------------------------------------------------------------------------- */
static void GripperStepper_TimerInit(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  uint32_t pclk1;
  uint32_t tim_clk;
  uint32_t prescaler;

  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_TIM4_CLK_ENABLE();

  /* PD14 = TIM4_CH3 STEP */
  GPIO_InitStruct.Pin = GPIO_PIN_14;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF2_TIM4;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

  /* PD15 = DIR, boot LOW */
  HAL_GPIO_WritePin(GRIPPER_DIR_GPIO_PORT, GRIPPER_DIR_GPIO_PIN, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = GRIPPER_DIR_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GRIPPER_DIR_GPIO_PORT, &GPIO_InitStruct);

  pclk1 = HAL_RCC_GetPCLK1Freq();
  if ((RCC->CFGR & RCC_CFGR_PPRE1) == RCC_HCLK_DIV1)
  {
    tim_clk = pclk1;
  }
  else
  {
    tim_clk = pclk1 * 2U;
  }

  /* Match the existing Stage_SetFrequency() assumption: 1 MHz timer tick. */
  prescaler = (tim_clk / STAGE_TIMER_TICK_HZ) - 1U;

  htim4.Instance = TIM4;
  htim4.Init.Prescaler = prescaler;
  htim4.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim4.Init.Period = 1000U - 1U;
  htim4.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim4.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;

  if (HAL_TIM_PWM_Init(&htim4) != HAL_OK)
  {
    Error_Handler();
  }

  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 500U;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;

  if (HAL_TIM_PWM_ConfigChannel(&htim4, &sConfigOC, GRIPPER_STEP_CHANNEL) != HAL_OK)
  {
    Error_Handler();
  }

  HAL_NVIC_SetPriority(TIM4_IRQn, 5U, 0U);
  HAL_NVIC_EnableIRQ(TIM4_IRQn);
}

static bool Stage_MinActive(const StageAxis *axis)
{
#if STAGE_USE_LIMIT_INPUTS
  if ((axis == NULL) || (axis->min_port == NULL) || (axis->min_pin == 0U))
  {
    return false;
  }
  return Stage_InputActive(axis->min_port, axis->min_pin,
                           STAGE_LIMIT_ACTIVE_LEVEL);
#else
  (void)axis;
  return false;
#endif
}

static bool Stage_MaxActive(const StageAxis *axis)
{
#if STAGE_USE_LIMIT_INPUTS
  if ((axis == NULL) || (axis->max_port == NULL) || (axis->max_pin == 0U))
  {
    return false;
  }
  return Stage_InputActive(axis->max_port, axis->max_pin,
                           STAGE_LIMIT_ACTIVE_LEVEL);
#else
  (void)axis;
  return false;
#endif
}

static bool Stage_IsGripperAxis(const StageAxis *axis)
{
  return (axis == &stage_axis[STAGE_AXIS_G]);
}

/*
 * G-axis limit debounce for timer ISR: require several consecutive timer
 * periods at HIGH. This avoids HAL_Delay() inside an interrupt while rejecting
 * the short spikes observed when the stepper driver switches. X/Z keep their
 * existing immediate limit behavior.
 */
static bool Stage_DestinationLimitConfirmedIsr(StageAxis *axis)
{
  bool active;
  volatile uint8_t *count;

  if (axis == NULL)
  {
    return false;
  }

  if (!Stage_IsGripperAxis(axis))
  {
    return axis->positive ? Stage_MaxActive(axis) : Stage_MinActive(axis);
  }

  active = axis->positive ? Stage_MaxActive(axis) : Stage_MinActive(axis);
  count = axis->positive ? &axis->max_limit_count : &axis->min_limit_count;

  if (active)
  {
    if (*count < GRIPPER_LIMIT_CONFIRM_PULSES)
    {
      ++(*count);
    }
  }
  else
  {
    *count = 0U;
  }

  return (*count >= GRIPPER_LIMIT_CONFIRM_PULSES);
}

static bool Stage_EStopInputActive(void)
{
#if STAGE_USE_ESTOP_INPUT
  return Stage_InputActive(STAGE_ESTOP_GPIO_PORT, STAGE_ESTOP_GPIO_PIN,
                           STAGE_ESTOP_ACTIVE_LEVEL);
#else
  return false;
#endif
}

static StageAxis *Stage_AxisOf(StageAxisId id)
{
  return (id < STAGE_AXIS_COUNT) ? &stage_axis[id] : NULL;
}

static void Stage_DriverEnable(StageAxis *axis, bool enable)
{
  if ((axis->ena_port != NULL) && (axis->ena_pin != 0U))
  {
    HAL_GPIO_WritePin(axis->ena_port, axis->ena_pin,
                      enable ? axis->enabled_level
                             : Stage_OppositeLevel(axis->enabled_level));
  }
  axis->enabled = enable;
}

static void Stage_SetDirection(StageAxis *axis, bool positive)
{
  HAL_GPIO_WritePin(axis->dir_port, axis->dir_pin,
                    positive ? axis->dir_positive_level
                             : Stage_OppositeLevel(axis->dir_positive_level));
  axis->positive = positive;
}

static uint32_t Stage_ClampHz(uint32_t hz)
{
  if (hz < STAGE_MIN_STEP_HZ)
  {
    return STAGE_MIN_STEP_HZ;
  }
  if (hz > STAGE_MAX_STEP_HZ)
  {
    return STAGE_MAX_STEP_HZ;
  }
  return hz;
}

static void Stage_SetFrequency(StageAxis *axis, uint32_t hz)
{
  uint32_t period;

  hz = Stage_ClampHz(hz);
  period = STAGE_TIMER_TICK_HZ / hz;
  if (period < 2U)
  {
    period = 2U;
  }

  __HAL_TIM_SET_AUTORELOAD(axis->htim, period - 1U);
  __HAL_TIM_SET_COMPARE(axis->htim, axis->channel, period / 2U);
  axis->current_hz = STAGE_TIMER_TICK_HZ / period;
}

/* 타이머 ISR 안에서도 호출되므로 HAL_Delay를 사용하지 않습니다. */
static void Stage_TimerStopIsr(StageAxis *axis)
{
  __HAL_TIM_DISABLE_IT(axis->htim, TIM_IT_UPDATE);
  (void)HAL_TIM_PWM_Stop(axis->htim, axis->channel);
  __HAL_TIM_CLEAR_FLAG(axis->htim, TIM_FLAG_UPDATE);
  axis->current_hz = 0U;
  axis->target_hz = 0U;
  axis->remaining_steps = 0U;
}

static void Stage_TimerStop(StageAxis *axis)
{
  uint32_t primask = __get_PRIMASK();

  __disable_irq();
  Stage_TimerStopIsr(axis);
  if (primask == 0U)
  {
    __enable_irq();
  }
}

static StageResult Stage_StartMotion(StageAxis *axis, bool positive,
                                     uint64_t steps, uint32_t target_hz,
                                     float accel_steps_s2, StageMode mode)
{
  if (stage_estop_latched || Stage_EStopInputActive())
  {
    return STAGE_ERR_ESTOP;
  }
  if (!axis->enabled)
  {
    return STAGE_ERR_DISABLED;
  }
  if (axis->mode != STAGE_MODE_IDLE)
  {
    return STAGE_ERR_BUSY;
  }
  if ((target_hz < STAGE_MIN_STEP_HZ) ||
      (target_hz > STAGE_MAX_STEP_HZ) ||
      !isfinite(accel_steps_s2) || (accel_steps_s2 <= 0.0f) ||
      (steps == 0U))
  {
    return STAGE_ERR_PARAM;
  }
  if (Stage_MinActive(axis) && Stage_MaxActive(axis))
  {
    return STAGE_ERR_LIMIT;
  }
  if ((!positive && Stage_MinActive(axis)) ||
      (positive && Stage_MaxActive(axis)))
  {
    return STAGE_ERR_LIMIT;
  }
  if (axis->homed &&
      ((!positive && (axis->position_steps <= axis->soft_min_steps)) ||
       (positive && (axis->position_steps >= axis->soft_max_steps))))
  {
    return STAGE_ERR_SOFT_LIMIT;
  }

  axis->min_limit_count = 0U;
  axis->max_limit_count = 0U;
  Stage_SetDirection(axis, positive);
  HAL_Delay(1U); /* DIR setup 시간과 드라이버 입력 안정화 시간 */

  axis->remaining_steps = steps;
  axis->target_hz = target_hz;
  axis->start_hz = STAGE_DEFAULT_START_HZ;
  if (axis->start_hz > target_hz)
  {
    axis->start_hz = target_hz;
  }
  axis->accel_steps_s2 = accel_steps_s2;
  axis->mode = mode;

  Stage_SetFrequency(axis, axis->start_hz);
  __HAL_TIM_SET_COUNTER(axis->htim, 0U);
  axis->htim->Instance->EGR = TIM_EGR_UG;
  __HAL_TIM_CLEAR_FLAG(axis->htim, TIM_FLAG_UPDATE);

  if (HAL_TIM_PWM_Start(axis->htim, axis->channel) != HAL_OK)
  {
    axis->mode = STAGE_MODE_FAULT;
    return STAGE_ERR_PARAM;
  }

  __HAL_TIM_ENABLE_IT(axis->htim, TIM_IT_UPDATE);
  __HAL_TIM_ENABLE(axis->htim);
  return STAGE_OK;
}

static void Stage_Init(TIM_HandleTypeDef *htim_x, TIM_HandleTypeDef *htim_z,
                       TIM_HandleTypeDef *htim_g)
{
  memset(stage_axis, 0, sizeof(stage_axis));

  stage_axis[STAGE_AXIS_X] = (StageAxis)
  {
    .htim = htim_x,
    .channel = STAGE_X_STEP_CHANNEL,
    .dir_port = STAGE_X_DIR_GPIO_PORT,
    .dir_pin = STAGE_X_DIR_GPIO_PIN,
    .ena_port = STAGE_X_ENA_GPIO_PORT,
    .ena_pin = STAGE_X_ENA_GPIO_PIN,
    .dir_positive_level = STAGE_X_DIR_POSITIVE_LEVEL,
    .enabled_level = STAGE_X_DRIVER_ENABLED_LEVEL,
    .min_port = STAGE_X_MIN_GPIO_PORT,
    .min_pin = STAGE_X_MIN_GPIO_PIN,
    .max_port = STAGE_X_MAX_GPIO_PORT,
    .max_pin = STAGE_X_MAX_GPIO_PIN,
    .mode = STAGE_MODE_IDLE,
    .steps_per_mm = STAGE_X_DEFAULT_STEPS_PER_MM,
    .soft_min_mm = STAGE_X_DEFAULT_MIN_MM,
    .soft_max_mm = STAGE_X_DEFAULT_MAX_MM,
    .soft_min_steps = (int64_t)(STAGE_X_DEFAULT_MIN_MM *
                                STAGE_X_DEFAULT_STEPS_PER_MM),
    .soft_max_steps = (int64_t)(STAGE_X_DEFAULT_MAX_MM *
                                STAGE_X_DEFAULT_STEPS_PER_MM)
  };

  stage_axis[STAGE_AXIS_Z] = (StageAxis)
  {
    .htim = htim_z,
    .channel = STAGE_Z_STEP_CHANNEL,
    .dir_port = STAGE_Z_DIR_GPIO_PORT,
    .dir_pin = STAGE_Z_DIR_GPIO_PIN,
    .ena_port = STAGE_Z_ENA_GPIO_PORT,
    .ena_pin = STAGE_Z_ENA_GPIO_PIN,
    .dir_positive_level = STAGE_Z_DIR_POSITIVE_LEVEL,
    .enabled_level = STAGE_Z_DRIVER_ENABLED_LEVEL,
    .min_port = STAGE_Z_MIN_GPIO_PORT,
    .min_pin = STAGE_Z_MIN_GPIO_PIN,
    .max_port = STAGE_Z_MAX_GPIO_PORT,
    .max_pin = STAGE_Z_MAX_GPIO_PIN,
    .mode = STAGE_MODE_IDLE,
    .steps_per_mm = STAGE_Z_DEFAULT_STEPS_PER_MM,
    .soft_min_mm = STAGE_Z_DEFAULT_MIN_MM,
    .soft_max_mm = STAGE_Z_DEFAULT_MAX_MM,
    .soft_min_steps = (int64_t)(STAGE_Z_DEFAULT_MIN_MM *
                                STAGE_Z_DEFAULT_STEPS_PER_MM),
    .soft_max_steps = (int64_t)(STAGE_Z_DEFAULT_MAX_MM *
                                STAGE_Z_DEFAULT_STEPS_PER_MM)
  };

  stage_axis[STAGE_AXIS_G] = (StageAxis)
  {
    .htim = htim_g,
    .channel = GRIPPER_STEP_CHANNEL,
    .dir_port = GRIPPER_DIR_GPIO_PORT,
    .dir_pin = GRIPPER_DIR_GPIO_PIN,
    .ena_port = NULL,
    .ena_pin = 0U,
    .dir_positive_level = GRIPPER_DIR_EXTEND_LEVEL,
    .enabled_level = GPIO_PIN_SET,
    .min_port = GRIPPER_RETRACT_LIMIT_GPIO_PORT,
    .min_pin = GRIPPER_RETRACT_LIMIT_GPIO_PIN,
    .max_port = GRIPPER_EXTEND_LIMIT_GPIO_PORT,
    .max_pin = GRIPPER_EXTEND_LIMIT_GPIO_PIN,
    .mode = STAGE_MODE_IDLE,
    .steps_per_mm = GRIPPER_DEFAULT_STEPS_PER_MM,
    .soft_min_mm = GRIPPER_SOFT_MIN_MM,
    .soft_max_mm = GRIPPER_SOFT_MAX_MM,
    .soft_min_steps = (int64_t)(GRIPPER_SOFT_MIN_MM *
                                GRIPPER_DEFAULT_STEPS_PER_MM),
    .soft_max_steps = (int64_t)(GRIPPER_SOFT_MAX_MM *
                                GRIPPER_DEFAULT_STEPS_PER_MM)
  };

  stage_estop_latched = Stage_EStopInputActive();
  Stage_DriverEnable(&stage_axis[STAGE_AXIS_X], false);
  Stage_DriverEnable(&stage_axis[STAGE_AXIS_Z], false);
  Stage_DriverEnable(&stage_axis[STAGE_AXIS_G], false);
}

static StageResult Stage_Enable(StageAxisId id, bool enable)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (enable && (stage_estop_latched || Stage_EStopInputActive()))
  {
    return STAGE_ERR_ESTOP;
  }
  if (!enable && (axis->mode != STAGE_MODE_IDLE))
  {
    Stage_TimerStop(axis);
    axis->mode = STAGE_MODE_IDLE;
  }
  Stage_DriverEnable(axis, enable);
  return STAGE_OK;
}

static StageResult Stage_MoveMm(StageAxisId id, float distance_mm,
                                float max_speed_mm_s, float accel_mm_s2)
{
  StageAxis *axis = Stage_AxisOf(id);
  int64_t signed_steps;
  float target_mm;
  uint64_t absolute_steps;

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (!isfinite(distance_mm) || !isfinite(max_speed_mm_s) ||
      !isfinite(accel_mm_s2) || (distance_mm == 0.0f) ||
      (max_speed_mm_s <= 0.0f) || (accel_mm_s2 <= 0.0f))
  {
    return STAGE_ERR_PARAM;
  }

  signed_steps = (int64_t)llroundf(distance_mm * axis->steps_per_mm);
  if (signed_steps == 0)
  {
    return STAGE_ERR_PARAM;
  }

  target_mm = ((float)axis->position_steps + (float)signed_steps) /
              axis->steps_per_mm;
  if (axis->homed &&
      ((target_mm < axis->soft_min_mm) || (target_mm > axis->soft_max_mm)))
  {
    return STAGE_ERR_SOFT_LIMIT;
  }

  absolute_steps = (uint64_t)((signed_steps > 0) ? signed_steps : -signed_steps);
  return Stage_StartMotion(axis, (signed_steps > 0), absolute_steps,
                           (uint32_t)lroundf(max_speed_mm_s * axis->steps_per_mm),
                           accel_mm_s2 * axis->steps_per_mm,
                           STAGE_MODE_MOVE);
}

static StageResult Stage_JogMmS(StageAxisId id, float signed_speed_mm_s,
                                float accel_mm_s2)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (!isfinite(signed_speed_mm_s) || !isfinite(accel_mm_s2) ||
      (signed_speed_mm_s == 0.0f) || (accel_mm_s2 <= 0.0f))
  {
    return STAGE_ERR_PARAM;
  }

  return Stage_StartMotion(axis, (signed_speed_mm_s > 0.0f), UINT64_MAX,
                           (uint32_t)lroundf(fabsf(signed_speed_mm_s) *
                                             axis->steps_per_mm),
                           accel_mm_s2 * axis->steps_per_mm,
                           STAGE_MODE_JOG);
}

static StageResult Stage_Home(StageAxisId id)
{
  StageAxis *axis = Stage_AxisOf(id);

#if !STAGE_USE_LIMIT_INPUTS
  (void)axis;
  return STAGE_ERR_PARAM;
#else
  uint32_t fast_hz;

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (stage_estop_latched || Stage_EStopInputActive())
  {
    return STAGE_ERR_ESTOP;
  }
  if (!axis->enabled)
  {
    return STAGE_ERR_DISABLED;
  }
  if (axis->mode != STAGE_MODE_IDLE)
  {
    return STAGE_ERR_BUSY;
  }
  if (Stage_MinActive(axis) && Stage_MaxActive(axis))
  {
    axis->mode = STAGE_MODE_FAULT;
    return STAGE_ERR_LIMIT;
  }

  axis->homed = false;
  if (Stage_MinActive(axis))
  {
    axis->mode = STAGE_MODE_HOME_WAIT_BACKOFF;
    return STAGE_OK;
  }

  if (id == STAGE_AXIS_G)
  {
    fast_hz = GRIPPER_HOME_STEP_HZ;
    return Stage_StartMotion(axis, false, UINT64_MAX, fast_hz,
                             GRIPPER_HOME_ACCEL_STEPS_S2,
                             STAGE_MODE_HOME_FAST);
  }

  fast_hz = (uint32_t)lroundf(STAGE_HOME_FAST_MM_S * axis->steps_per_mm);
  return Stage_StartMotion(axis, false, UINT64_MAX, fast_hz,
                           STAGE_HOME_ACCEL_MM_S2 * axis->steps_per_mm,
                           STAGE_MODE_HOME_FAST);
#endif
}

static StageResult Stage_Stop(StageAxisId id, bool hard)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (axis->mode == STAGE_MODE_IDLE)
  {
    return STAGE_OK;
  }
  if (hard || (axis->current_hz <= axis->start_hz))
  {
    Stage_TimerStop(axis);
    axis->mode = STAGE_MODE_IDLE;
  }
  else
  {
    axis->mode = STAGE_MODE_SOFT_STOP;
    axis->remaining_steps = UINT64_MAX;
  }
  return STAGE_OK;
}

static void Stage_StopAll(bool hard)
{
  (void)Stage_Stop(STAGE_AXIS_X, hard);
  (void)Stage_Stop(STAGE_AXIS_Z, hard);
  (void)Stage_Stop(STAGE_AXIS_G, hard);
}

static void Stage_EStop(void)
{
  uint32_t i;

  stage_estop_latched = true;
  for (i = 0U; i < STAGE_AXIS_COUNT; ++i)
  {
    Stage_TimerStop(&stage_axis[i]);
    stage_axis[i].mode = STAGE_MODE_FAULT;
    Stage_DriverEnable(&stage_axis[i], false);
    stage_axis[i].homed = false;
  }
}

static StageResult Stage_ResetEStop(void)
{
  uint32_t i;

  if (Stage_EStopInputActive())
  {
    return STAGE_ERR_ESTOP;
  }

  stage_estop_latched = false;
  for (i = 0U; i < STAGE_AXIS_COUNT; ++i)
  {
    Stage_TimerStop(&stage_axis[i]);
    stage_axis[i].mode = STAGE_MODE_IDLE;
    stage_axis[i].homed = false;
  }
  return STAGE_OK;
}

static StageResult Stage_Zero(StageAxisId id)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (axis->mode != STAGE_MODE_IDLE)
  {
    return STAGE_ERR_BUSY;
  }
  axis->position_steps = 0;
  axis->homed = true;
  return STAGE_OK;
}

static StageResult Stage_SetStepsPerMm(StageAxisId id, float steps_per_mm)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (axis->mode != STAGE_MODE_IDLE)
  {
    return STAGE_ERR_BUSY;
  }
  if (!isfinite(steps_per_mm) || (steps_per_mm <= 0.0f) ||
      (steps_per_mm > 1000000.0f))
  {
    return STAGE_ERR_PARAM;
  }

  axis->steps_per_mm = steps_per_mm;
  axis->soft_min_steps = (int64_t)llroundf(axis->soft_min_mm * steps_per_mm);
  axis->soft_max_steps = (int64_t)llroundf(axis->soft_max_mm * steps_per_mm);
  axis->homed = false;
  axis->position_steps = 0;
  return STAGE_OK;
}

static StageResult Stage_SetSoftLimitsMm(StageAxisId id, float min_mm,
                                         float max_mm)
{
  StageAxis *axis = Stage_AxisOf(id);

  if (axis == NULL)
  {
    return STAGE_ERR_AXIS;
  }
  if (axis->mode != STAGE_MODE_IDLE)
  {
    return STAGE_ERR_BUSY;
  }
  if (!isfinite(min_mm) || !isfinite(max_mm) || (min_mm >= max_mm))
  {
    return STAGE_ERR_PARAM;
  }

  axis->soft_min_mm = min_mm;
  axis->soft_max_mm = max_mm;
  axis->soft_min_steps = (int64_t)llroundf(min_mm * axis->steps_per_mm);
  axis->soft_max_steps = (int64_t)llroundf(max_mm * axis->steps_per_mm);
  return STAGE_OK;
}

static void Stage_FinishAxisIsr(StageAxis *axis)
{
  Stage_TimerStopIsr(axis);
  axis->mode = STAGE_MODE_IDLE;
}

static void Stage_OnTimerPeriodElapsed(TIM_HandleTypeDef *htim)
{
  StageAxis *axis = NULL;
  uint32_t i;

  for (i = 0U; i < STAGE_AXIS_COUNT; ++i)
  {
    if (stage_axis[i].htim == htim)
    {
      axis = &stage_axis[i];
      break;
    }
  }

  if ((axis == NULL) || (axis->mode == STAGE_MODE_IDLE) ||
      (axis->mode == STAGE_MODE_FAULT))
  {
    return;
  }

  if (Stage_DestinationLimitConfirmedIsr(axis))
  {
    StageMode previous_mode = axis->mode;

    Stage_TimerStopIsr(axis);
    if ((previous_mode == STAGE_MODE_HOME_FAST) && !axis->positive)
    {
      axis->mode = STAGE_MODE_HOME_WAIT_BACKOFF;
    }
    else if ((previous_mode == STAGE_MODE_HOME_SLOW) && !axis->positive)
    {
      if (Stage_IsGripperAxis(axis))
      {
        axis->mode = STAGE_MODE_HOME_WAIT_FINAL_BACKOFF;
      }
      else
      {
        axis->position_steps = axis->soft_min_steps;
        axis->homed = true;
        axis->mode = STAGE_MODE_IDLE;
      }
    }
    else
    {
      axis->homed = false;
      axis->mode = STAGE_MODE_FAULT;
    }
    return;
  }

  if (axis->homed &&
      ((!axis->positive && (axis->position_steps <= axis->soft_min_steps)) ||
       (axis->positive && (axis->position_steps >= axis->soft_max_steps))))
  {
    Stage_TimerStopIsr(axis);
    axis->mode = STAGE_MODE_IDLE;
    return;
  }

  axis->position_steps += axis->positive ? 1 : -1;
  if (axis->remaining_steps != UINT64_MAX)
  {
    if (axis->remaining_steps > 0U)
    {
      --axis->remaining_steps;
    }
    if (axis->remaining_steps == 0U)
    {
      if (axis->mode == STAGE_MODE_HOME_BACKOFF)
      {
        Stage_TimerStopIsr(axis);
        axis->mode = STAGE_MODE_HOME_WAIT_SLOW;
      }
      else if (axis->mode == STAGE_MODE_HOME_FINAL_BACKOFF)
      {
        Stage_TimerStopIsr(axis);
        if (Stage_MinActive(axis))
        {
          axis->homed = false;
          axis->mode = STAGE_MODE_FAULT;
        }
        else
        {
          axis->position_steps = 0;
          axis->homed = true;
          axis->mode = STAGE_MODE_IDLE;
        }
      }
      else
      {
        Stage_FinishAxisIsr(axis);
      }
    }
  }
}

static void Stage_ProcessAxis10ms(StageAxis *axis)
{
  uint32_t next_hz;
  float delta_hz;
  uint64_t stopping_steps = 0U;

  if (axis->mode == STAGE_MODE_HOME_WAIT_BACKOFF)
  {
    axis->mode = STAGE_MODE_IDLE;
    if (Stage_IsGripperAxis(axis))
    {
      (void)Stage_StartMotion(
        axis, true,
        (uint64_t)llroundf(GRIPPER_HOME_BACKOFF_MM * axis->steps_per_mm),
        GRIPPER_HOME_STEP_HZ,
        GRIPPER_HOME_ACCEL_STEPS_S2,
        STAGE_MODE_HOME_BACKOFF);
    }
    else
    {
      (void)Stage_StartMotion(
        axis, true,
        (uint64_t)llroundf(STAGE_HOME_BACKOFF_MM * axis->steps_per_mm),
        (uint32_t)lroundf(STAGE_HOME_SLOW_MM_S * axis->steps_per_mm),
        STAGE_HOME_ACCEL_MM_S2 * axis->steps_per_mm,
        STAGE_MODE_HOME_BACKOFF);
    }
    return;
  }

  if (axis->mode == STAGE_MODE_HOME_WAIT_SLOW)
  {
    axis->mode = STAGE_MODE_IDLE;
    if (Stage_IsGripperAxis(axis))
    {
      (void)Stage_StartMotion(
        axis, false, UINT64_MAX,
        GRIPPER_HOME_STEP_HZ / 2U,
        GRIPPER_HOME_ACCEL_STEPS_S2,
        STAGE_MODE_HOME_SLOW);
    }
    else
    {
      (void)Stage_StartMotion(
        axis, false, UINT64_MAX,
        (uint32_t)lroundf(STAGE_HOME_SLOW_MM_S * axis->steps_per_mm),
        STAGE_HOME_ACCEL_MM_S2 * axis->steps_per_mm,
        STAGE_MODE_HOME_SLOW);
    }
    return;
  }

  if (axis->mode == STAGE_MODE_HOME_WAIT_FINAL_BACKOFF)
  {
    axis->mode = STAGE_MODE_IDLE;
    (void)Stage_StartMotion(
      axis, true,
      (uint64_t)llroundf(GRIPPER_HOME_BACKOFF_MM * axis->steps_per_mm),
      GRIPPER_HOME_STEP_HZ,
      GRIPPER_HOME_ACCEL_STEPS_S2,
      STAGE_MODE_HOME_FINAL_BACKOFF);
    return;
  }

  if ((axis->mode == STAGE_MODE_IDLE) ||
      (axis->mode == STAGE_MODE_FAULT) ||
      (axis->current_hz == 0U))
  {
    return;
  }

  delta_hz = axis->accel_steps_s2 *
             ((float)STAGE_CONTROL_PERIOD_MS / 1000.0f);
  if (delta_hz < 1.0f)
  {
    delta_hz = 1.0f;
  }
  next_hz = axis->current_hz;

  if ((axis->accel_steps_s2 > 0.0f) &&
      (axis->current_hz > axis->start_hz))
  {
    float stop = (((float)axis->current_hz * (float)axis->current_hz) -
                  ((float)axis->start_hz * (float)axis->start_hz)) /
                 (2.0f * axis->accel_steps_s2);
    if (stop > 0.0f)
    {
      stopping_steps = (uint64_t)ceilf(stop);
    }
    stopping_steps += ((uint64_t)axis->current_hz + 49U) / 50U + 2U;
  }

  if ((axis->mode == STAGE_MODE_SOFT_STOP) ||
      ((axis->remaining_steps != UINT64_MAX) &&
       (axis->remaining_steps <= stopping_steps)))
  {
    if ((float)axis->current_hz <= ((float)axis->start_hz + delta_hz))
    {
      /*
       * HOME BACKOFF는 목표 거리까지 반드시 완료한다.
       * 감속 과정에서 조기 IDLE로 종료되면
       * HOME_WAIT_SLOW -> HOME_SLOW 재접근이 수행되지 않는다.
       */
      if ((axis->mode == STAGE_MODE_HOME_BACKOFF) ||
          (axis->mode == STAGE_MODE_HOME_FINAL_BACKOFF))
      {
        if (axis->current_hz != axis->start_hz)
        {
          Stage_SetFrequency(axis, axis->start_hz);
        }
        return;
      }

      Stage_TimerStop(axis);
      axis->mode = STAGE_MODE_IDLE;
      return;
    }
    next_hz = (uint32_t)((float)axis->current_hz - delta_hz);
    if (next_hz < axis->start_hz)
    {
      next_hz = axis->start_hz;
    }
  }
  else if (axis->current_hz < axis->target_hz)
  {
    next_hz = (uint32_t)((float)axis->current_hz + delta_hz);
    if (next_hz > axis->target_hz)
    {
      next_hz = axis->target_hz;
    }
  }

  if (next_hz != axis->current_hz)
  {
    Stage_SetFrequency(axis, next_hz);
  }
}

static void Stage_Process10ms(void)
{
  if (Stage_EStopInputActive())
  {
    if (!stage_estop_latched)
    {
      Stage_EStop();
    }
    return;
  }

  Stage_ProcessAxis10ms(&stage_axis[STAGE_AXIS_X]);
  Stage_ProcessAxis10ms(&stage_axis[STAGE_AXIS_Z]);
  Stage_ProcessAxis10ms(&stage_axis[STAGE_AXIS_G]);
}

static void Stage_GetStatus(StageAxisId id, StageAxisStatus *out)
{
  StageAxis *axis = Stage_AxisOf(id);
  uint32_t primask;

  if ((axis == NULL) || (out == NULL))
  {
    return;
  }

  primask = __get_PRIMASK();
  __disable_irq();
  out->mode = axis->mode;
  out->position_steps = axis->position_steps;
  out->remaining_steps = axis->remaining_steps;
  out->current_hz = axis->current_hz;
  out->target_hz = axis->target_hz;
  out->steps_per_mm = axis->steps_per_mm;
  out->enabled = axis->enabled;
  out->homed = axis->homed;
  if (primask == 0U)
  {
    __enable_irq();
  }

  out->position_mm = (float)out->position_steps / out->steps_per_mm;
  out->min_limit = Stage_MinActive(axis);
  out->max_limit = Stage_MaxActive(axis);
}

static const char *Stage_ModeName(StageMode mode)
{
  static const char *const names[] =
  {
    "IDLE", "MOVE", "JOG", "HOME_FAST", "HOME_WAIT_BACKOFF",
    "HOME_BACKOFF", "HOME_WAIT_SLOW", "HOME_SLOW",
    "HOME_WAIT_FINAL_BACKOFF", "HOME_FINAL_BACKOFF", "SOFT_STOP", "FAULT"
  };

  return (mode <= STAGE_MODE_FAULT) ? names[mode] : "UNKNOWN";
}

static const char *Stage_ResultName(StageResult result)
{
  static const char *const names[] =
  {
    "OK", "BAD_AXIS", "BUSY", "DISABLED", "ESTOP", "LIMIT",
    "SOFT_LIMIT", "BAD_PARAM"
  };

  return (result <= STAGE_ERR_PARAM) ? names[result] : "UNKNOWN";
}

static void StageProtocol_SendText(const char *text)
{
  if ((stage_uart == NULL) || (text == NULL))
  {
    return;
  }
  (void)HAL_UART_Transmit(stage_uart, (uint8_t *)text,
                          (uint16_t)strlen(text), 100U);
}

static void StageProtocol_Reply(StageResult result)
{
  char output[64];

  (void)snprintf(output, sizeof(output), "%s %s\r\n",
                 (result == STAGE_OK) ? "OK" : "ERR",
                 Stage_ResultName(result));
  StageProtocol_SendText(output);
}

static bool StageProtocol_ParseAxis(const char *text, StageAxisId *id)
{
  if ((text == NULL) || (id == NULL))
  {
    return false;
  }
  if ((text[0] == 'X') && (text[1] == '\0'))
  {
    *id = STAGE_AXIS_X;
    return true;
  }
  if ((text[0] == 'Z') && (text[1] == '\0'))
  {
    *id = STAGE_AXIS_Z;
    return true;
  }
  return false;
}

static char *StageProtocol_NextToken(char **save)
{
  return strtok_r(NULL, " \t", save);
}

static void StageProtocol_Uppercase(char *text)
{
  while ((text != NULL) && (*text != '\0'))
  {
    *text = (char)toupper((unsigned char)*text);
    ++text;
  }
}

/* newlib-nano의 printf float 옵션 없이도 JSON 소수 위치를 출력합니다. */
static void StageProtocol_FormatMm(float mm, char *output, size_t output_size)
{
  int64_t scaled = (int64_t)llroundf(mm * 10000.0f);
  uint64_t magnitude;

  if (scaled < 0)
  {
    magnitude = (uint64_t)(-(scaled + 1)) + 1U;
    (void)snprintf(output, output_size, "-%lu.%04lu",
                   (unsigned long)(magnitude / 10000U),
                   (unsigned long)(magnitude % 10000U));
  }
  else
  {
    magnitude = (uint64_t)scaled;
    (void)snprintf(output, output_size, "%lu.%04lu",
                   (unsigned long)(magnitude / 10000U),
                   (unsigned long)(magnitude % 10000U));
  }
}

static void StageProtocol_SendStatus(void)
{
  StageAxisStatus x;
  StageAxisStatus z;
  char x_position[32];
  char z_position[32];
  char output[512];

  Stage_GetStatus(STAGE_AXIS_X, &x);
  Stage_GetStatus(STAGE_AXIS_Z, &z);
  StageProtocol_FormatMm(x.position_mm, x_position, sizeof(x_position));
  StageProtocol_FormatMm(z.position_mm, z_position, sizeof(z_position));

  (void)snprintf(
    output, sizeof(output),
    "{\"type\":\"status\",\"estop\":%u,"
    "\"x\":{\"mode\":\"%s\",\"pos_mm\":%s,\"steps\":%ld,"
    "\"hz\":%lu,\"enabled\":%u,\"homed\":%u,\"min\":%u,\"max\":%u},"
    "\"z\":{\"mode\":\"%s\",\"pos_mm\":%s,\"steps\":%ld,"
    "\"hz\":%lu,\"enabled\":%u,\"homed\":%u,\"min\":%u,\"max\":%u}}\r\n",
    stage_estop_latched ? 1U : 0U,
    Stage_ModeName(x.mode), x_position, (long)x.position_steps,
    (unsigned long)x.current_hz, x.enabled ? 1U : 0U, x.homed ? 1U : 0U,
    x.min_limit ? 1U : 0U, x.max_limit ? 1U : 0U,
    Stage_ModeName(z.mode), z_position, (long)z.position_steps,
    (unsigned long)z.current_hz, z.enabled ? 1U : 0U, z.homed ? 1U : 0U,
    z.min_limit ? 1U : 0U, z.max_limit ? 1U : 0U);
  StageProtocol_SendText(output);
}

static void StageProtocol_SendGripperStatus(void)
{
  StageAxisStatus g;
  char position[32];
  char output[160];

  Stage_GetStatus(STAGE_AXIS_G, &g);
  StageProtocol_FormatMm(g.position_mm, position, sizeof(position));

  (void)snprintf(output, sizeof(output),
                 "GRIPPER STATUS %s POS_MM %s STEPS %ld HZ %lu ENABLED %u HOMED %u RETRACT_LIMIT %u EXTEND_LIMIT %u\r\n",
                 Stage_ModeName(g.mode), position, (long)g.position_steps,
                 (unsigned long)g.current_hz, g.enabled ? 1U : 0U,
                 g.homed ? 1U : 0U, g.min_limit ? 1U : 0U,
                 g.max_limit ? 1U : 0U);
  StageProtocol_SendText(output);
}

static void StageProtocol_HandleLine(char *line)
{
  char *save = NULL;
  char *command = strtok_r(line, " \t", &save);
  char *axis_text;
  StageAxisId axis;
  StageResult result = STAGE_ERR_PARAM;

  if (command == NULL)
  {
    return;
  }
  StageProtocol_Uppercase(command);

  if (strcmp(command, "PING") == 0)
  {
    StageProtocol_SendText("OK PONG\r\n");
    return;
  }
  if (strcmp(command, "STATUS") == 0)
  {
    StageProtocol_SendStatus();
    return;
  }
  if (strcmp(command, "LOAD") == 0)
  {
    int32_t raw = HX711_1_ReadRaw();
    char output[96];

    if (raw == INT32_MIN)
    {
      StageProtocol_SendText("ERR HX711_TIMEOUT\r\n");
    }
    else
    {
      float weight_g =
          ((float)raw - HX711_1_TARE_RAW) / HX711_1_COUNT_PER_G;

      int32_t weight_x10 = (int32_t)lroundf(weight_g * 10.0f);
      int32_t weight_abs = (weight_x10 < 0) ? -weight_x10 : weight_x10;

      (void)snprintf(
          output,
          sizeof(output),
          "LOAD_RAW %ld WEIGHT_G %s%ld.%01ld\r\n",
          (long)raw,
          (weight_x10 < 0) ? "-" : "",
          (long)(weight_abs / 10),
          (long)(weight_abs % 10));

      StageProtocol_SendText(output);
    }
    return;
  }
  if (strcmp(command, "FINAL_LOAD") == 0)
  {
    char *action = strtok_r(NULL, " \t", &save);
    char output[160];

    if (action == NULL)
    {
      StageProtocol_SendText(
          "ERR FINAL_LOAD BAD_PARAM\r\n");
      return;
    }

    StageProtocol_Uppercase(action);

    if (strcmp(action, "RAW") == 0)
    {
      int32_t raw = HX711_2_ReadRaw();

      if (raw == INT32_MIN)
      {
        StageProtocol_SendText(
            "ERR FINAL_HX711_TIMEOUT\r\n");
      }
      else
      {
        (void)snprintf(
            output,
            sizeof(output),
            "FINAL_LOAD_RAW %ld\r\n",
            (long)raw);

        StageProtocol_SendText(output);
      }

      return;
    }

    if (strcmp(action, "TARE") == 0)
    {
      float average_raw;

      if (!HX711_2_ReadAverage(
              HX711_2_DEFAULT_SAMPLES,
              &average_raw))
      {
        StageProtocol_SendText(
            "ERR FINAL_HX711_TIMEOUT\r\n");
        return;
      }

      HX711_2_TareRaw = average_raw;
      HX711_2_TareValid = true;

      /* TARE는 영점(offset)만 갱신한다.
       * 기존 calibration scale(COUNT_PER_G)은 유지한다. */
      int32_t tare_x10 =
          (int32_t)lroundf(HX711_2_TareRaw * 10.0f);
      int32_t tare_abs =
          (tare_x10 < 0) ? -tare_x10 : tare_x10;

      (void)snprintf(
          output,
          sizeof(output),
          "OK FINAL_LOAD TARE RAW %s%ld.%01ld SAMPLES %lu\r\n",
          (tare_x10 < 0) ? "-" : "",
          (long)(tare_abs / 10),
          (long)(tare_abs % 10),
          (unsigned long)HX711_2_DEFAULT_SAMPLES);

      StageProtocol_SendText(output);
      return;
    }

    if (strcmp(action, "CAL") == 0)
    {
      char *grams_text =
          strtok_r(NULL, " \t", &save);

      float known_weight_g;
      float average_raw;
      float count_per_g;

      if (grams_text == NULL)
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD CAL_PARAM\r\n");
        return;
      }

      known_weight_g =
          strtof(grams_text, NULL);

      if (known_weight_g <= 0.0f)
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD CAL_RANGE\r\n");
        return;
      }

      if (!HX711_2_TareValid)
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD NOT_TARED\r\n");
        return;
      }

      if (!HX711_2_ReadAverage(
              HX711_2_DEFAULT_SAMPLES,
              &average_raw))
      {
        StageProtocol_SendText(
            "ERR FINAL_HX711_TIMEOUT\r\n");
        return;
      }

      count_per_g =
          (average_raw - HX711_2_TareRaw)
          / known_weight_g;

      /* 배선 방향에 따라 음수가 되는 것은 정상이다.
       * 단, 거의 0이면 calibration 실패로 본다. */
      if ((count_per_g > -0.001f) &&
          (count_per_g < 0.001f))
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD CAL_ZERO\r\n");
        return;
      }

      HX711_2_CountPerG = count_per_g;
      HX711_2_CalValid = true;

      int32_t known_x10 =
          (int32_t)lroundf(known_weight_g * 10.0f);
      int32_t known_abs =
          (known_x10 < 0) ? -known_x10 : known_x10;

      int32_t count_x10000 =
          (int32_t)lroundf(HX711_2_CountPerG * 10000.0f);
      int32_t count_abs =
          (count_x10000 < 0) ? -count_x10000 : count_x10000;

      (void)snprintf(
          output,
          sizeof(output),
          "OK FINAL_LOAD CAL WEIGHT_G %s%ld.%01ld COUNT_PER_G %s%ld.%04ld\r\n",
          (known_x10 < 0) ? "-" : "",
          (long)(known_abs / 10),
          (long)(known_abs % 10),
          (count_x10000 < 0) ? "-" : "",
          (long)(count_abs / 10000),
          (long)(count_abs % 10000));

      StageProtocol_SendText(output);
      return;
    }

    if (strcmp(action, "WEIGHT") == 0)
    {
      float average_raw;
      float weight_g;

      if (!HX711_2_TareValid)
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD NOT_TARED\r\n");
        return;
      }

      if (!HX711_2_CalValid)
      {
        StageProtocol_SendText(
            "ERR FINAL_LOAD NOT_CALIBRATED\r\n");
        return;
      }

      if (!HX711_2_ReadAverage(
              HX711_2_DEFAULT_SAMPLES,
              &average_raw))
      {
        StageProtocol_SendText(
            "ERR FINAL_HX711_TIMEOUT\r\n");
        return;
      }

      weight_g =
          (average_raw - HX711_2_TareRaw)
          / HX711_2_CountPerG;

      int32_t weight_x10 =
          (int32_t)lroundf(weight_g * 10.0f);
      int32_t weight_abs =
          (weight_x10 < 0) ? -weight_x10 : weight_x10;

      int32_t raw_x10 =
          (int32_t)lroundf(average_raw * 10.0f);
      int32_t raw_abs =
          (raw_x10 < 0) ? -raw_x10 : raw_x10;

      (void)snprintf(
          output,
          sizeof(output),
          "FINAL_LOAD WEIGHT_G %s%ld.%01ld RAW_AVG %s%ld.%01ld SAMPLES %lu\r\n",
          (weight_x10 < 0) ? "-" : "",
          (long)(weight_abs / 10),
          (long)(weight_abs % 10),
          (raw_x10 < 0) ? "-" : "",
          (long)(raw_abs / 10),
          (long)(raw_abs % 10),
          (unsigned long)HX711_2_DEFAULT_SAMPLES);

      StageProtocol_SendText(output);
      return;
    }

    if (strcmp(action, "STATUS") == 0)
    {
      int32_t tare_x10 =
          (int32_t)lroundf(HX711_2_TareRaw * 10.0f);
      int32_t tare_abs =
          (tare_x10 < 0) ? -tare_x10 : tare_x10;

      int32_t count_x10000 =
          (int32_t)lroundf(HX711_2_CountPerG * 10000.0f);
      int32_t count_abs =
          (count_x10000 < 0) ? -count_x10000 : count_x10000;

      (void)snprintf(
          output,
          sizeof(output),
          "FINAL_LOAD STATUS TARED %u CALIBRATED %u TARE_RAW %s%ld.%01ld COUNT_PER_G %s%ld.%04ld\r\n",
          HX711_2_TareValid ? 1U : 0U,
          HX711_2_CalValid ? 1U : 0U,
          (tare_x10 < 0) ? "-" : "",
          (long)(tare_abs / 10),
          (long)(tare_abs % 10),
          (count_x10000 < 0) ? "-" : "",
          (long)(count_abs / 10000),
          (long)(count_abs % 10000));

      StageProtocol_SendText(output);
      return;
    }

    StageProtocol_SendText(
        "ERR FINAL_LOAD BAD_PARAM\r\n");
    return;
  }

  if (strcmp(command, "GRIP") == 0)
  {
    char *action = strtok_r(NULL, " \t", &save);
    char output[64];
    uint32_t angle;

    if (action == NULL)
    {
      StageProtocol_SendText("ERR GRIP_PARAM\r\n");
      return;
    }

    StageProtocol_Uppercase(action);

    if (strcmp(action, "OPEN") == 0)
    {
      angle = GRIP_OPEN_ANGLE_DEG;
    }
    else if (strcmp(action, "CLOSE") == 0)
    {
      angle = GRIP_CLOSE_ANGLE_DEG;
    }
    else
    {
      StageProtocol_SendText("ERR GRIP_PARAM\r\n");
      return;
    }

    Servo_SetAngle(angle);

    (void)snprintf(
        output,
        sizeof(output),
        "OK GRIP %s %lu\r\n",
        action,
        (unsigned long)angle);

    StageProtocol_SendText(output);
    return;
  }

  if (strcmp(command, "GRIPPER") == 0)
  {
    char *action = StageProtocol_NextToken(&save);
    StageResult g_result;
    StageAxis *g_axis = &stage_axis[STAGE_AXIS_G];
    char output[96];

    if (action == NULL)
    {
      StageProtocol_SendText("ERR GRIPPER BAD_PARAM\r\n");
      return;
    }
    StageProtocol_Uppercase(action);

    if (strcmp(action, "STATUS") == 0)
    {
      StageProtocol_SendGripperStatus();
      return;
    }

    if (strcmp(action, "STOP") == 0)
    {
      g_result = Stage_Stop(STAGE_AXIS_G, true);
      if (g_result == STAGE_OK)
      {
        StageProtocol_SendText("OK GRIPPER STOP\r\n");
      }
      else
      {
        (void)snprintf(output, sizeof(output), "ERR GRIPPER %s\r\n",
                       Stage_ResultName(g_result));
        StageProtocol_SendText(output);
      }
      return;
    }

    if ((strcmp(action, "HOME") != 0) &&
        (strcmp(action, "EXTEND") != 0) &&
        (strcmp(action, "RETRACT") != 0))
    {
      StageProtocol_SendText("ERR GRIPPER BAD_PARAM\r\n");
      return;
    }

    if (!g_axis->enabled)
    {
      g_result = Stage_Enable(STAGE_AXIS_G, true);
      if (g_result != STAGE_OK)
      {
        (void)snprintf(output, sizeof(output), "ERR GRIPPER %s\r\n",
                       Stage_ResultName(g_result));
        StageProtocol_SendText(output);
        return;
      }
    }

    if (strcmp(action, "HOME") == 0)
    {
      /* Unexpected limit fault invalidates position. HOME is the recovery path. */
      if ((g_axis->mode == STAGE_MODE_FAULT) && !stage_estop_latched)
      {
        Stage_TimerStop(g_axis);
        g_axis->mode = STAGE_MODE_IDLE;
        g_axis->homed = false;
      }

      g_result = Stage_Home(STAGE_AXIS_G);
      if (g_result == STAGE_OK)
      {
        StageProtocol_SendText("OK GRIPPER HOME\r\n");
      }
      else
      {
        (void)snprintf(output, sizeof(output), "ERR GRIPPER %s\r\n",
                       Stage_ResultName(g_result));
        StageProtocol_SendText(output);
      }
      return;
    }

    if (!g_axis->homed)
    {
      StageProtocol_SendText("ERR GRIPPER NOT_HOMED\r\n");
      return;
    }

    {
      float target_mm = (strcmp(action, "EXTEND") == 0)
                          ? GRIPPER_EXTEND_POSITION_MM
                          : GRIPPER_RETRACT_POSITION_MM;
      float current_mm = (float)g_axis->position_steps / g_axis->steps_per_mm;
      float delta_mm = target_mm - current_mm;

      if (fabsf(delta_mm) < 0.05f)
      {
        (void)snprintf(output, sizeof(output), "OK GRIPPER %s\r\n", action);
        StageProtocol_SendText(output);
        return;
      }

      g_result = Stage_MoveMm(
          STAGE_AXIS_G,
          delta_mm,
          GRIPPER_DEFAULT_SPEED_MM_S,
          GRIPPER_DEFAULT_ACCEL_MM_S2);
    }

    if (g_result == STAGE_OK)
    {
      (void)snprintf(output, sizeof(output), "OK GRIPPER %s\r\n", action);
    }
    else
    {
      (void)snprintf(output, sizeof(output), "ERR GRIPPER %s\r\n",
                     Stage_ResultName(g_result));
    }
    StageProtocol_SendText(output);
    return;
  }

  if (strcmp(command, "SERVO") == 0)
  {
    char *arg = strtok_r(NULL, " \t", &save);
    char output[64];
    long angle;

    if (arg == NULL)
    {
      StageProtocol_SendText("ERR SERVO_PARAM\r\n");
      return;
    }

    angle = strtol(arg, NULL, 10);

    if ((angle < 0L) || (angle > 180L))
    {
      StageProtocol_SendText("ERR SERVO_RANGE\r\n");
      return;
    }

    Servo_SetAngle((uint32_t)angle);

    (void)snprintf(output,
                   sizeof(output),
                   "OK SERVO %ld\r\n",
                   angle);
    StageProtocol_SendText(output);
    return;
  }

  if (strcmp(command, "ESTOP") == 0)
  {
    Stage_EStop();
    StageProtocol_SendText("OK ESTOP\r\n");
    return;
  }
  if (strcmp(command, "RESET") == 0)
  {
    StageProtocol_Reply(Stage_ResetEStop());
    return;
  }

  axis_text = StageProtocol_NextToken(&save);
  if (axis_text == NULL)
  {
    StageProtocol_Reply(STAGE_ERR_PARAM);
    return;
  }
  StageProtocol_Uppercase(axis_text);

  if ((strcmp(axis_text, "ALL") == 0) && (strcmp(command, "STOP") == 0))
  {
    char *kind = StageProtocol_NextToken(&save);
    if (kind != NULL)
    {
      StageProtocol_Uppercase(kind);
    }
    Stage_StopAll((kind == NULL) || (strcmp(kind, "SOFT") != 0));
    StageProtocol_Reply(STAGE_OK);
    return;
  }

  if (!StageProtocol_ParseAxis(axis_text, &axis))
  {
    StageProtocol_Reply(STAGE_ERR_AXIS);
    return;
  }

  if (strcmp(command, "ENABLE") == 0)
  {
    char *value = StageProtocol_NextToken(&save);
    if (value != NULL)
    {
      result = Stage_Enable(axis, strtol(value, NULL, 10) != 0L);
    }
  }
  else if (strcmp(command, "MOVE") == 0)
  {
    char *distance = StageProtocol_NextToken(&save);
    char *speed = StageProtocol_NextToken(&save);
    char *accel = StageProtocol_NextToken(&save);
    if ((distance != NULL) && (speed != NULL) && (accel != NULL))
    {
      result = Stage_MoveMm(axis, strtof(distance, NULL), strtof(speed, NULL),
                            strtof(accel, NULL));
    }
  }
  else if (strcmp(command, "JOG") == 0)
  {
    char *speed = StageProtocol_NextToken(&save);
    char *accel = StageProtocol_NextToken(&save);
    if ((speed != NULL) && (accel != NULL))
    {
      result = Stage_JogMmS(axis, strtof(speed, NULL), strtof(accel, NULL));
    }
  }
  else if (strcmp(command, "HOME") == 0)
  {
    result = Stage_Home(axis);
  }
  else if (strcmp(command, "STOP") == 0)
  {
    char *kind = StageProtocol_NextToken(&save);
    if (kind != NULL)
    {
      StageProtocol_Uppercase(kind);
    }
    result = Stage_Stop(axis, (kind == NULL) || (strcmp(kind, "SOFT") != 0));
  }
  else if (strcmp(command, "ZERO") == 0)
  {
    result = Stage_Zero(axis);
  }
  else if (strcmp(command, "SET_STEPS_PER_MM") == 0)
  {
    char *value = StageProtocol_NextToken(&save);
    if (value != NULL)
    {
      result = Stage_SetStepsPerMm(axis, strtof(value, NULL));
    }
  }
  else if (strcmp(command, "SET_LIMITS") == 0)
  {
    char *minimum = StageProtocol_NextToken(&save);
    char *maximum = StageProtocol_NextToken(&save);
    if ((minimum != NULL) && (maximum != NULL))
    {
      result = Stage_SetSoftLimitsMm(axis, strtof(minimum, NULL),
                                     strtof(maximum, NULL));
    }
  }

  StageProtocol_Reply(result);
}

static void StageProtocol_Init(UART_HandleTypeDef *huart)
{
  stage_uart = huart;
  stage_rx_head = 0U;
  stage_rx_tail = 0U;
  (void)HAL_UART_Receive_IT(stage_uart, &stage_rx_byte, 1U);
}

static void StageProtocol_OnRxComplete(UART_HandleTypeDef *huart)
{
  uint16_t next;

  if (huart != stage_uart)
  {
    return;
  }

  next = (uint16_t)((stage_rx_head + 1U) % STAGE_RX_RING_SIZE);
  if (next != stage_rx_tail)
  {
    stage_rx_ring[stage_rx_head] = (char)stage_rx_byte;
    stage_rx_head = next;
  }
  (void)HAL_UART_Receive_IT(stage_uart, &stage_rx_byte, 1U);
}

static void StageProtocol_Process(void)
{
  static char line[STAGE_LINE_SIZE];
  static uint16_t length = 0U;

  while (stage_rx_tail != stage_rx_head)
  {
    char character = stage_rx_ring[stage_rx_tail];
    stage_rx_tail = (uint16_t)((stage_rx_tail + 1U) % STAGE_RX_RING_SIZE);

    if (character == '\r')
    {
      continue;
    }
    if (character == '\n')
    {
      if (length > 0U)
      {
        line[length] = '\0';
        StageProtocol_HandleLine(line);
        length = 0U;
      }
    }
    else if (length < (STAGE_LINE_SIZE - 1U))
    {
      line[length++] = character;
    }
    else
    {
      length = 0U;
      StageProtocol_SendText("ERR LINE_TOO_LONG\r\n");
    }
  }
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MPU Configuration--------------------------------------------------------*/
  MPU_Config();

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_TIM1_Init();
  MX_USART3_UART_Init();
  MX_TIM8_Init();
  /* USER CODE BEGIN 2 */
  /*
   * 현재 SystemClock_Config()의 APB2/TIM1/TIM8 입력은 16 MHz입니다.
   * STAGE_TIMER_TICK_HZ=1 MHz와 맞추기 위해 PSC=15를 적용합니다.
   * 시스템 클록을 바꾸면 두 PSC와 STAGE_TIMER_TICK_HZ를 함께 맞추십시오.
   */
  __HAL_TIM_SET_PRESCALER(&htim1, 15U);
  __HAL_TIM_SET_PRESCALER(&htim8, 15U);

  /* main.c 하나만으로도 필요한 인터럽트가 활성화되도록 합니다. */
  HAL_NVIC_SetPriority(TIM1_UP_TIM10_IRQn, 5U, 0U);
  HAL_NVIC_EnableIRQ(TIM1_UP_TIM10_IRQn);
  HAL_NVIC_SetPriority(TIM8_UP_TIM13_IRQn, 5U, 0U);
  HAL_NVIC_EnableIRQ(TIM8_UP_TIM13_IRQn);
  HAL_NVIC_SetPriority(USART3_IRQn, 5U, 0U);
  HAL_NVIC_EnableIRQ(USART3_IRQn);
#if STAGE_USE_ESTOP_INPUT
  HAL_NVIC_SetPriority(EXTI2_IRQn, 4U, 0U);
  HAL_NVIC_EnableIRQ(EXTI2_IRQn);
#endif

  /* Gripper STEP timer is manually initialized, matching Servo_Init style. */
  GripperStepper_TimerInit();

  /* 모든 GPIO/타이머/UART 초기화가 끝난 뒤 스테이지 제어를 시작합니다. */
  Stage_Init(&htim1, &htim8, &htim4);
  StageProtocol_Init(&huart3);
  stage_last_10ms = HAL_GetTick();
/* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* Windows GUI에서 수신한 한 줄 명령을 처리합니다. */
    StageProtocol_Process();

    /* 두 축의 가속·감속과 E-STOP 입력을 10 ms마다 처리합니다. */
    if ((uint32_t)(HAL_GetTick() - stage_last_10ms) >= STAGE_CONTROL_PERIOD_MS)
    {
      stage_last_10ms += STAGE_CONTROL_PERIOD_MS;
      Stage_Process10ms();
    }

    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */

  /* USER CODE END TIM1_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */

  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 15;
  htim1.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim1.Init.Period = 65535;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 0;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim1, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */

  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

}

/**
  * @brief TIM8 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM8_Init(void)
{

  /* USER CODE BEGIN TIM8_Init 0 */

  /* USER CODE END TIM8_Init 0 */

  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM8_Init 1 */

  /* USER CODE END TIM8_Init 1 */
  htim8.Instance = TIM8;
  htim8.Init.Prescaler = 15;
  htim8.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim8.Init.Period = 65535;
  htim8.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim8.Init.RepetitionCounter = 0;
  htim8.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim8, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim8, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 0;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim8, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM8_Init 2 */

  /* USER CODE END TIM8_Init 2 */
  HAL_TIM_MspPostInit(&htim8);

}

/**
  * @brief USART3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART3_UART_Init(void)
{

  /* USER CODE BEGIN USART3_Init 0 */

  /* USER CODE END USART3_Init 0 */

  /* USER CODE BEGIN USART3_Init 1 */

  /* USER CODE END USART3_Init 1 */
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  huart3.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart3.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART3_Init 2 */

  /* USER CODE END USART3_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
/* USER CODE BEGIN MX_GPIO_Init_1 */
/* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOE_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOG_CLK_ENABLE();

  /* DIR은 LOW, ENA는 드라이버 비활성 상태로 부팅합니다. */
  HAL_GPIO_WritePin(GPIOD, GPIO_PIN_4|GPIO_PIN_6, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(GPIOD, GPIO_PIN_5|GPIO_PIN_7, GPIO_PIN_SET);

  /*Configure GPIO pins : PD4 PD5 PD6 PD7 */
  GPIO_InitStruct.Pin = GPIO_PIN_4|GPIO_PIN_5|GPIO_PIN_6|GPIO_PIN_7;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOD, &GPIO_InitStruct);

/* USER CODE BEGIN MX_GPIO_Init_2 */
  /* HX711 #1: PG0=DOUT(input), PG1=SCK(output, idle LOW) */
  GPIO_InitStruct.Pin = HX711_1_DOUT_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(HX711_1_DOUT_GPIO_PORT, &GPIO_InitStruct);

  HAL_GPIO_WritePin(HX711_1_SCK_GPIO_PORT,
                    HX711_1_SCK_GPIO_PIN,
                    GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = HX711_1_SCK_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(HX711_1_SCK_GPIO_PORT, &GPIO_InitStruct);

  /* HX711 #2: PG3=DOUT(input), PG4=SCK(output, idle LOW) */
  GPIO_InitStruct.Pin = HX711_2_DOUT_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(HX711_2_DOUT_GPIO_PORT, &GPIO_InitStruct);

  HAL_GPIO_WritePin(HX711_2_SCK_GPIO_PORT,
                    HX711_2_SCK_GPIO_PIN,
                    GPIO_PIN_RESET);

  GPIO_InitStruct.Pin = HX711_2_SCK_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(HX711_2_SCK_GPIO_PORT, &GPIO_InitStruct);

#if STAGE_USE_LIMIT_INPUTS
  /* Gripper NC limits: normal LOW, active/disconnected HIGH */
  __HAL_RCC_GPIOE_CLK_ENABLE();
  GPIO_InitStruct.Pin = GRIPPER_RETRACT_LIMIT_GPIO_PIN |
                        GRIPPER_EXTEND_LIMIT_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOE, &GPIO_InitStruct);

  /* NC 리미트 4개: 정상 LOW, 작동/단선 HIGH */
  GPIO_InitStruct.Pin = GPIO_PIN_12|GPIO_PIN_13|GPIO_PIN_14|GPIO_PIN_15;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOF, &GPIO_InitStruct);
#endif

#if STAGE_USE_ESTOP_INPUT
  /* NC E-STOP 감시 입력: LOW->HIGH에서 즉시 소프트웨어 E-STOP */
  GPIO_InitStruct.Pin = STAGE_ESTOP_GPIO_PIN;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(STAGE_ESTOP_GPIO_PORT, &GPIO_InitStruct);
#endif
/* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
/**
  * @brief Gripper STEP timer interrupt.
  * Startup vector의 weak TIM4_IRQHandler를 이 strong definition이 대체합니다.
  */
void TIM4_IRQHandler(void)
{
  HAL_TIM_IRQHandler(&htim4);
}

/**
  * @brief TIM1/TIM8/TIM4의 한 펄스 주기가 끝날 때 위치와 잔여 펄스를 갱신합니다.
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  Stage_OnTimerPeriodElapsed(htim);
}

/**
  * @brief USART3에서 1바이트 수신이 완료되면 다음 바이트 수신을 이어갑니다.
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  StageProtocol_OnRxComplete(huart);
}

/**
  * @brief USART3 수신 오류 후 1바이트 인터럽트 수신을 복구합니다.
  */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
  if (huart == stage_uart)
  {
    (void)HAL_UART_AbortReceive(huart);
    (void)HAL_UART_Receive_IT(huart, &stage_rx_byte, 1U);
  }
}

/**
  * @brief 물리 E-STOP EXTI 입력이 발생하면 즉시 소프트웨어 정지를 래치합니다.
  */
void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)
{
#if STAGE_USE_ESTOP_INPUT
  if (GPIO_Pin == STAGE_ESTOP_GPIO_PIN)
  {
    Stage_EStop();
  }
#else
  (void)GPIO_Pin;
#endif
}

/*
 * CubeMX의 stm32f7xx_it.c에 같은 IRQ 함수가 이미 있으면 그 강한 정의가
 * 우선합니다. 없는 경우에는 아래 weak 핸들러 덕분에 main.c 하나만으로
 * HAL 콜백까지 연결됩니다.
 */
__weak void TIM1_UP_TIM10_IRQHandler(void)
{
  HAL_TIM_IRQHandler(&htim1);
}

__weak void TIM8_UP_TIM13_IRQHandler(void)
{
  HAL_TIM_IRQHandler(&htim8);
}

__weak void USART3_IRQHandler(void)
{
  HAL_UART_IRQHandler(&huart3);
}

__weak void EXTI2_IRQHandler(void)
{
#if STAGE_USE_ESTOP_INPUT
  HAL_GPIO_EXTI_IRQHandler(STAGE_ESTOP_GPIO_PIN);
#endif
}
/* USER CODE END 4 */

 /* MPU Configuration */

void MPU_Config(void)
{
  MPU_Region_InitTypeDef MPU_InitStruct = {0};

  /* Disables the MPU */
  HAL_MPU_Disable();

  /** Initializes and configures the Region and the memory to be protected
  */
  MPU_InitStruct.Enable = MPU_REGION_ENABLE;
  MPU_InitStruct.Number = MPU_REGION_NUMBER0;
  MPU_InitStruct.BaseAddress = 0x0;
  MPU_InitStruct.Size = MPU_REGION_SIZE_4GB;
  MPU_InitStruct.SubRegionDisable = 0x87;
  MPU_InitStruct.TypeExtField = MPU_TEX_LEVEL0;
  MPU_InitStruct.AccessPermission = MPU_REGION_NO_ACCESS;
  MPU_InitStruct.DisableExec = MPU_INSTRUCTION_ACCESS_DISABLE;
  MPU_InitStruct.IsShareable = MPU_ACCESS_SHAREABLE;
  MPU_InitStruct.IsCacheable = MPU_ACCESS_NOT_CACHEABLE;
  MPU_InitStruct.IsBufferable = MPU_ACCESS_NOT_BUFFERABLE;

  HAL_MPU_ConfigRegion(&MPU_InitStruct);
  /* Enables the MPU */
  HAL_MPU_Enable(MPU_PRIVILEGED_DEFAULT);

}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}

#ifdef  USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
