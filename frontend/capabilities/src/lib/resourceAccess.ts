import type { AccessActorContext, ResourceScopeFields } from '../api/types'

export const SHARED_RESOURCE_READ_ONLY_HINT = '共享资源只能查看和使用，不能修改'
export const SHARED_RESOURCE_BADGE_CLASS = 'bg-info-soft text-info-soft-fg'

export function canModifyResource(
  actor: AccessActorContext | null | undefined,
  resource: Pick<ResourceScopeFields, 'owner_group_key'> | null | undefined,
): boolean {
  if (!actor || !resource) return false
  return actor.is_maintenance_admin || Boolean(actor.group_key && actor.group_key === resource.owner_group_key)
}

export function isSharedResourceReadOnly(
  actor: AccessActorContext | null | undefined,
  resource: ResourceScopeFields | null | undefined,
): boolean {
  return Boolean(resource?.visibility === 'shared' && !canModifyResource(actor, resource))
}
