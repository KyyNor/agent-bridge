import { nextTick, onUnmounted } from 'vue'
import { driver, type DriveStep, type Driver, type PopoverDOM } from 'driver.js'
import { api } from '../api/client'
import type { OnboardingTourStatus } from '../api/types'
import type { ProductTourDefinition, ProductTourStep } from '../lib/onboardingTours'

const TOUR_TARGET_WAIT_FRAMES = 12

function toDriverStep(step: ProductTourStep): DriveStep {
  return { element: step.element, popover: step.popover }
}

function nextFrame(): Promise<void> {
  return new Promise(resolve => requestAnimationFrame(() => resolve()))
}

async function waitForTourTargets(tour: ProductTourDefinition): Promise<DriveStep[]> {
  await nextTick()
  let availableSteps: DriveStep[] = []
  for (let frame = 0; frame < TOUR_TARGET_WAIT_FRAMES; frame += 1) {
    availableSteps = tour.steps.filter(step => document.querySelector(step.element)).map(toDriverStep)
    if (availableSteps.length === tour.steps.length) return availableSteps
    await nextFrame()
  }
  return availableSteps
}

/**
 * 通用 Driver.js 导览控制器。定义放在 lib，用户状态由后端按 actor + key + version 保存。
 */
export function useOnboardingTour() {
  let activeDriver: Driver | null = null
  let suppressPersist = false
  const automaticChecks = new Set<string>()

  async function persist(tour: ProductTourDefinition, status: OnboardingTourStatus) {
    try {
      await api.saveOnboardingTourProgress(tour.key, tour.version, status)
    } catch (error) {
      // 导览不应因状态保存失败而卡住；下次访问会再次提供导览。
      console.warn('保存新手导览状态失败，将在下次访问时重新提供导览', error)
    }
  }

  function appendSkipButton(popover: PopoverDOM, skip: () => void) {
    if (popover.footerButtons.querySelector('[data-tour-skip]')) return
    const button = document.createElement('button')
    button.type = 'button'
    button.dataset.tourSkip = 'true'
    button.className = 'driver-popover-footer-btn driver-popover-prev-btn agent-bridge-tour-skip'
    button.textContent = '跳过导览'
    button.addEventListener('click', skip)
    popover.footerButtons.prepend(button)
  }

  async function startTour(tour: ProductTourDefinition): Promise<boolean> {
    if (activeDriver?.isActive()) return false
    // 权限、空数据或路由状态可能让某些入口不存在；仅展示已稳定出现的步骤。
    const availableSteps = await waitForTourTargets(tour)
    if (!availableSteps.length) return false

    let finished = false
    const finish = (status: OnboardingTourStatus) => {
      if (finished) return
      finished = true
      void persist(tour, status)
      activeDriver?.destroy()
    }
    const tourDriver = driver({
      steps: availableSteps,
      animate: true,
      allowClose: true,
      overlayClickBehavior: 'close',
      smoothScroll: true,
      showProgress: true,
      progressText: '{{current}} / {{total}}',
      nextBtnText: '下一步',
      prevBtnText: '上一步',
      doneBtnText: '完成',
      showButtons: ['next', 'previous', 'close'],
      popoverClass: 'agent-bridge-tour-popover',
      onPopoverRender: popover => appendSkipButton(popover, () => finish('skipped')),
      onNextClick: (_element, _step, options) => {
        if (options.driver.isLastStep()) finish('completed')
        else options.driver.moveNext()
      },
      onDoneClick: () => finish('completed'),
      onCloseClick: () => finish('skipped'),
      onDestroyStarted: () => {
        if (!suppressPersist && !finished) finish('skipped')
        else tourDriver.destroy()
      },
      onDestroyed: () => {
        if (activeDriver === tourDriver) activeDriver = null
      },
    })
    activeDriver = tourDriver
    tourDriver.drive()
    return true
  }

  async function maybeStartTour(tour: ProductTourDefinition): Promise<boolean> {
    const identity = `${tour.key}:${tour.version}`
    if (automaticChecks.has(identity)) return false
    automaticChecks.add(identity)
    try {
      const progress = await api.getOnboardingTourProgress(tour.key, tour.version)
      return progress.should_show ? startTour(tour) : false
    } catch (error) {
      console.warn('读取新手导览状态失败，暂不自动启动导览', error)
      return false
    }
  }

  function stopTour() {
    if (!activeDriver?.isActive()) return
    suppressPersist = true
    activeDriver.destroy()
    suppressPersist = false
  }

  onUnmounted(stopTour)
  return { maybeStartTour, startTour, stopTour }
}
