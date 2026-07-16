import type { ProfilePinRule, ProfileResourceRule, ProfileSourceRule } from '../../api/types'

export interface ProfileConfigDraft {
  sourceRules: ProfileSourceRule[]
  resourceRules: ProfileResourceRule[]
  memoryBlockKey: string
  pins: ProfilePinRule[]
  pinMode: 'disabled' | 'ratio' | 'count'
  pinRatio: number
  pinCount: number
  manualNotes: string
}

function sortByKey<T>(items: T[], key: (item: T) => string): T[] {
  return [...items].sort((left, right) => key(left).localeCompare(key(right)))
}

export function profileConfigDraftKey(draft: ProfileConfigDraft): string {
  return JSON.stringify({
    sourceRules: sortByKey(
      draft.sourceRules,
      rule => `${rule.source_type}:${rule.source_key}:${rule.effect}`,
    ),
    resourceRules: sortByKey(
      draft.resourceRules,
      rule => `${rule.resource_type}:${rule.resource_key}`,
    ),
    memoryBlockKey: draft.memoryBlockKey,
    pins: sortByKey(
      draft.pins,
      pin => `${pin.service_key}:${pin.tool_type}`,
    ),
    pinMode: draft.pinMode,
    pinRatio: draft.pinRatio,
    pinCount: draft.pinCount,
    manualNotes: draft.manualNotes,
  })
}
