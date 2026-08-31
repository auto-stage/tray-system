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

/* PUL: X=PA8/TIM1_CH1, Z=PC6/TIM8_CH1 (CubeMX 설정 유지) */
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

#define STAGE_CONTROL_PERIOD_MS            10U
#define STAGE_RX_RING_SIZE                 512U
#define STAGE_LINE_SIZE                    192U

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

TIM_HandleTypeDef htim1;
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
static void Stage_Init(TIM_HandleTypeDef *htim_x, TIM_HandleTypeDef *htim_z);
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

static bool Stage_MinActive(const StageAxis *axis)
{
#if STAGE_USE_LIMIT_INPUTS
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
  return Stage_InputActive(axis->max_port, axis->max_pin,
                           STAGE_LIMIT_ACTIVE_LEVEL);
#else
  (void)axis;
  return false;
#endif
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
  HAL_GPIO_WritePin(axis->ena_port, axis->ena_pin,
                    enable ? axis->enabled_level
                           : Stage_OppositeLevel(axis->enabled_level));
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

static void Stage_Init(TIM_HandleTypeDef *htim_x, TIM_HandleTypeDef *htim_z)
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

  stage_estop_latched = Stage_EStopInputActive();
  Stage_DriverEnable(&stage_axis[STAGE_AXIS_X], false);
  Stage_DriverEnable(&stage_axis[STAGE_AXIS_Z], false);
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

  if ((!axis->positive && Stage_MinActive(axis)) ||
      (axis->positive && Stage_MaxActive(axis)))
  {
    StageMode previous_mode = axis->mode;

    Stage_TimerStopIsr(axis);
    if ((previous_mode == STAGE_MODE_HOME_FAST) && !axis->positive)
    {
      axis->mode = STAGE_MODE_HOME_WAIT_BACKOFF;
    }
    else if ((previous_mode == STAGE_MODE_HOME_SLOW) && !axis->positive)
    {
      axis->position_steps = axis->soft_min_steps;
      axis->homed = true;
      axis->mode = STAGE_MODE_IDLE;
    }
    else
    {
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
    (void)Stage_StartMotion(
      axis, true,
      (uint64_t)llroundf(STAGE_HOME_BACKOFF_MM * axis->steps_per_mm),
      (uint32_t)lroundf(STAGE_HOME_SLOW_MM_S * axis->steps_per_mm),
      STAGE_HOME_ACCEL_MM_S2 * axis->steps_per_mm,
      STAGE_MODE_HOME_BACKOFF);
    return;
  }

  if (axis->mode == STAGE_MODE_HOME_WAIT_SLOW)
  {
    axis->mode = STAGE_MODE_IDLE;
    (void)Stage_StartMotion(
      axis, false, UINT64_MAX,
      (uint32_t)lroundf(STAGE_HOME_SLOW_MM_S * axis->steps_per_mm),
      STAGE_HOME_ACCEL_MM_S2 * axis->steps_per_mm,
      STAGE_MODE_HOME_SLOW);
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
    "HOME_BACKOFF", "HOME_WAIT_SLOW", "HOME_SLOW", "SOFT_STOP", "FAULT"
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

  /* 모든 GPIO/타이머/UART 초기화가 끝난 뒤 스테이지 제어를 시작합니다. */
  Stage_Init(&htim1, &htim8);
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
#if STAGE_USE_LIMIT_INPUTS
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
  * @brief TIM1/TIM8의 한 펄스 주기가 끝날 때 위치와 잔여 펄스를 갱신합니다.
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
