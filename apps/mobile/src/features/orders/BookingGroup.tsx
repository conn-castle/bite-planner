import { AppLauncher } from '@capacitor/app-launcher'
import { useIonAlert } from '@ionic/react'
import type { BookFields, BookState, ISODate } from '../../lib/api/types'
import { formatDate, formatWindow } from '../../lib/dates'
import { logger } from '../../lib/log'
import { formatMoney } from '../../lib/products'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { BOOK_STATE_LABEL, DRY_RUN, changeStep, changeable } from './booking'
import { useBookAgain, useCancelOrder, useChangeOrder, useSettleOrder } from './orders'

// The household's orders on Instacart's website (measured 2026-10-01: "Order history").
const INSTACART_ORDERS = 'https://www.instacart.com/store/account/orders'

interface BookingGroupProps {
  order: BookFields & { id: string; number: number; delivery_date: ISODate | null; book_state: BookState }
  /** It's the household's draft again, to change: open the flow at this step (else where it got to). */
  onChanged: (step: 'delivery' | 'list' | undefined) => void
  /** It was cancelled (it's gone). */
  onCancelled?: () => void
}

/**
 * Where an approved order is with the store, and what can be done about it: what
 * Instacart confirmed once it's booked; while it isn't booked yet, changing it
 * (back through the flow, approved again) or cancelling it; booking it again
 * when it wasn't; and, when Place order was pressed without a confirmation,
 * saying whether Instacart has it. Shared by the flow (after approving) and the
 * order's page.
 */
export function BookingGroup({ order, onChanged, onCancelled }: BookingGroupProps) {
  const [presentAlert] = useIonAlert()
  const change = useChangeOrder()
  const cancel = useCancelOrder()
  const bookAgain = useBookAgain()
  const settle = useSettleOrder()
  const state = order.book_state
  const booked = order.booking
  const busy = change.isPending || cancel.isPending || bookAgain.isPending || settle.isPending
  const failed = change.error ?? cancel.error ?? bookAgain.error ?? settle.error

  function changeIt() {
    const step = changeStep(state)
    change.mutate({ orderId: order.id }, { onSuccess: () => onChanged(step) })
  }

  function confirmCancel() {
    void presentAlert({
      header: `Cancel Order ${order.number}?`,
      message: "Nothing has been ordered yet. It's deleted for everyone in the household.",
      buttons: [
        { text: 'Keep it', role: 'cancel' },
        {
          text: 'Cancel order',
          role: 'destructive',
          handler: () => cancel.mutate({ orderId: order.id }, { onSuccess: () => onCancelled?.() }),
        },
      ],
    })
  }

  function openInstacart() {
    void AppLauncher.openUrl({ url: INSTACART_ORDERS }).catch((error: unknown) =>
      logger.warn('orders.open_instacart_failed', { error: String(error) }),
    )
  }

  return (
    <>
      {failed && (
        <Notice>Couldn't do that: {failed.message}</Notice>
      )}
      <Group
        title="Instacart"
        footer={
          state === 'booked' && booked?.swaps.length
            ? booked.swaps.map((swap) => `${swap.gone} was gone, so it's ${swap.bought} instead.`).join(' ')
            : state === 'waiting'
              ? 'You can change or cancel it until it books.'
              : order.book_issue?.message
        }
      >
        <Row label="Status" detail={BOOK_STATE_LABEL[state]} />
        {state === 'waiting' && order.book_on && (
          <Row label="Books" sub="Four days before it arrives" detail={formatDate(order.book_on, 'medium')} />
        )}
        {state === 'booked' && booked && (
          <>
            {booked.order_number && (
              <Row
                label="Instacart order"
                detail={booked.order_number}
                sub={booked.order_number === DRY_RUN ? 'Nothing was ordered (development)' : undefined}
              />
            )}
            {booked.window && order.delivery_date && (
              <Row
                label="Arrives"
                detail={`${formatDate(order.delivery_date, 'medium')}, ${formatWindow(booked.window)}`}
              />
            )}
            {booked.total_cents !== null && <Row label="Charged" detail={formatMoney(booked.total_cents)} />}
          </>
        )}
        {state === 'not_booked' && (
          <Row label="Try again" chevron disabled={busy} onClick={() => bookAgain.mutate({ orderId: order.id })} />
        )}
        {state === 'check_instacart' && (
          <>
            <Row
              label="It was placed"
              sub="Instacart has it"
              chevron
              disabled={busy}
              onClick={() => settle.mutate({ orderId: order.id, placed: true })}
            />
            <Row
              label="It wasn't placed"
              sub="Nothing was ordered; book it again"
              chevron
              disabled={busy}
              onClick={() => settle.mutate({ orderId: order.id, placed: false })}
            />
          </>
        )}
        {changeable(state) && (
          <Row
            label={
              state === 'needs_time'
                ? 'Choose another time'
                : state === 'needs_product'
                  ? 'Choose another product'
                  : 'Change order'
            }
            chevron
            disabled={busy}
            onClick={changeIt}
          />
        )}
        {(state === 'booked' || state === 'check_instacart') && booked?.order_number !== DRY_RUN && (
          <Row label="Open Instacart orders" chevron onClick={openInstacart} />
        )}
        {changeable(state) && (
          <Row label="Cancel order" tone="danger" disabled={busy} onClick={confirmCancel} />
        )}
      </Group>
    </>
  )
}
