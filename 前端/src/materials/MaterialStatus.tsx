import { MATERIAL_STATUS_LABEL } from './materials'

const statusHelp: Readonly<Record<string, string>> = {
  citable: '生成时可用这段摘录做依据。',
  link_only: '补上摘录后才能用于生成。',
  unusable: '补一段可读文字后才能用于生成。',
}

export function MaterialStatus({ status }: { status: string }) {
  return (
    <>
      <span className={'badge ' + (status === 'citable' ? 'ok' : 'warn')}>
        {MATERIAL_STATUS_LABEL[status]}
      </span>
      <span className="mat-status"> {statusHelp[status]}</span>
    </>
  )
}
