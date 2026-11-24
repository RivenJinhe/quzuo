import type { Candidate, Material } from '../types'

export const MATERIAL_LIMIT = 20

export type ArrangeMaterialChange =
  | { kind: 'saved'; material: Material }
  | { kind: 'updated'; materials: Material[] }
  | { kind: 'deleted'; ids: string[] }

export interface Selection {
  candidates: Candidate[]
  ids: string[]
  confirmed: boolean
  limit: number
}

export function prepareSelection(
  candidates: Candidate[],
  previousIds: readonly string[],
  limit: number,
): Selection {
  const priority = new Set(previousIds)
  for (const candidate of candidates) {
    if (candidate.preselected) priority.add(candidate.material_id)
  }
  const eligible = candidates.filter((candidate) => candidate.usable)
  const ids = [
    ...eligible.filter((candidate) => priority.has(candidate.material_id)),
    ...eligible.filter((candidate) => !priority.has(candidate.material_id)),
  ]
    .slice(0, limit)
    .map((candidate) => candidate.material_id)
  return { candidates, ids, limit, confirmed: false }
}

function fromMaterial(material: Material): Candidate {
  return {
    material_id: material.id,
    title: material.title,
    topic: material.topic,
    status: material.status,
    usable: material.status === 'citable' && Boolean(material.excerpt.trim()),
    unusable_reason: null,
    reason: material.note ? `你写过：「${material.note}」` : '从收藏手动选择',
  }
}

export function addMaterials(selection: Selection, materials: Material[]) {
  const candidates = new Map(selection.candidates.map((candidate) => [candidate.material_id, candidate]))
  const ids = new Set(selection.ids)
  let limitReached = false
  for (const material of materials) {
    const candidate = fromMaterial(material)
    candidates.set(material.id, candidate)
    if (!candidate.usable) ids.delete(material.id)
    else if (!ids.has(material.id)) {
      if (ids.size < selection.limit) ids.add(material.id)
      else limitReached = true
    }
  }
  return {
    selection: { ...selection, candidates: [...candidates.values()], ids: [...ids] },
    limitReached,
  }
}

export function removeMaterials(selection: Selection, removed: ReadonlySet<string>): Selection {
  const candidates = selection.candidates.filter((candidate) => !removed.has(candidate.material_id))
  if (candidates.length === selection.candidates.length) return selection
  const ids = selection.ids.filter((id) => !removed.has(id))
  return {
    ...selection,
    candidates,
    ids,
    confirmed: selection.confirmed && ids.length === selection.ids.length,
  }
}

export function updateMaterials(selection: Selection, materials: Material[]): Selection {
  const candidates = new Map(selection.candidates.map((candidate) => [candidate.material_id, candidate]))
  const ids = new Set(selection.ids)
  let changed = false
  let confirmed = selection.confirmed
  for (const material of materials) {
    const previous = candidates.get(material.id)
    if (!previous) continue
    const candidate = fromMaterial(material)
    candidates.set(material.id, candidate)
    changed = true
    if (!candidate.usable && ids.delete(material.id)) confirmed = false
    if (candidate.usable && !previous.usable && !confirmed && ids.size < selection.limit) {
      ids.add(material.id)
    }
  }
  return changed
    ? { ...selection, candidates: [...candidates.values()], ids: [...ids], confirmed }
    : selection
}
