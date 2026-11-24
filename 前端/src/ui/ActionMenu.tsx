import { Fragment, type ReactNode } from 'react'
import * as Menu from '@radix-ui/react-dropdown-menu'

interface MenuProps {
  label: string
  className?: string
  children?: ReactNode
  disabled?: boolean
  items: { label: string; onSelect: () => void; danger?: boolean }[]
}

export function ActionMenu({ label, className, children = '···', disabled, items }: MenuProps) {
  return (
    <Menu.Root modal={false}>
      <Menu.Trigger
        type="button"
        aria-label={label}
        title={label}
        className={className}
        disabled={disabled}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          className="pop"
          align="end"
          sideOffset={6}
          collisionPadding={8}
          onClick={(e) => e.stopPropagation()}
        >
          {items.map((item, i) => (
            <Fragment key={item.label}>
              {i > 0 && item.danger && !items[i - 1].danger && (
                <Menu.Separator asChild>
                  <hr />
                </Menu.Separator>
              )}
              <Menu.Item asChild onSelect={item.onSelect}>
                <button type="button" className={item.danger ? 'danger' : undefined}>
                  {item.label}
                </button>
              </Menu.Item>
            </Fragment>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}
