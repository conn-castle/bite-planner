import { AppLauncher } from '@capacitor/app-launcher'
import { useIonAlert } from '@ionic/react'
import { useState } from 'react'
import type { BookFields, BookState, ISODate } from '../../lib/api/types'
import { addDays, dateRange, formatClockTime, formatDate, formatWindow } from '../../lib/dates'
import { logger } from '../../lib/log'
import { formatMoney } from '../../lib/products'
import { useNow } from '../../lib/useNow'
import { DateBar } from '../../ui/DateBar'
import { Group, Row } from '../../ui/Group'
import { Notice } from '../../ui/Notice'
import { BOOK_STATE_LABEL, DRY_RUN, changeStep, changeable } from './booking'
import { useApproveDelivery, useBookAgain, useCancelOrder, useChangeOrder, useDeferDelivery, useSettleOrder } from './orders'

// The household's orders on Instacart's website (measured 2026-10-01: "Order history").
const INSTACART_ORDERS = 'https://www.instacart.com/store/account/orders'

interface BookingGroupProps {
  order: BookFields & { id: string; number: number; delivery_date: ISODate | null; next_delivery_date: ISODate | null; book_state: BookState }
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
  const approve = useApproveDelivery()
  const defer = useDeferDelivery()
  const now = useNow(1000)
  const [chosenNext, setChosenNext] = useState<ISODate | null>(null)
  const state = order.book_state
  const booked = order.booking
  const busy = change.isPending || cancel.isPending || bookAgain.isPending || settle.isPending || approve.isPending || defer.isPending
  const failed = change.error ?? cancel.error ?? bookAgain.error ?? settle.error ?? approve.error ?? defer.error
  const proposal = state === 'needs_time' ? order.book_issue?.proposal : null
  const next: ISODate | null = chosenNext ?? order.next_delivery_date
  const nextValid: boolean = proposal != null && next !== null && next > proposal.day
  const proposalExpired: boolean = proposal != null && (now.getTime() >= new Date(proposal.starts_at).getTime() || now.getTime() >= new Date(proposal.valid_until).getTime())

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
      {proposal && <Group title="Approve a new delivery" footer="Nothing has been ordered. This alternative only books after you approve it.">
        <Row label={formatDate(proposal.day, 'long')} detail={formatWindow(proposal.window)} sub={`Arrive by ${formatClockTime(proposal.window.end)}`} />
        {!nextValid && <Row label="Choose the next delivery date" sub="The new delivery falls on or after your previous next delivery. Choose when the following order should arrive." />}
        {(!order.next_delivery_date || order.next_delivery_date <= proposal.day) && <DateBar
          label="Next delivery date" selected={nextValid ? next : null}
          days={dateRange(addDays(proposal.day, 1), addDays(proposal.day, 14)).map((date) => ({ date, selectable: true, coveredBy: null, tag: null }))}
          onChoose={setChosenNext}
        />}
        {nextValid && next && <Row label="Next delivery" detail={formatDate(next, 'medium')} />}
        <Row label={proposalExpired ? 'Check times again' : 'Approve delivery'} chevron disabled={busy || (!proposalExpired && !nextValid)} onClick={() => {
          if (proposalExpired) bookAgain.mutate({ orderId: order.id })
          else if (next && nextValid) approve.mutate({ orderId: order.id, proposalId: proposal.id, nextDelivery: next })
        }} />
        <Row label={order.book_issue?.deferred_at ? 'Waiting for your decision' : 'Decide later'} sub="The order stays pending. Nothing is booked automatically." disabled={busy || order.book_issue?.deferred_at != null} onClick={() => defer.mutate({ orderId: order.id, proposalId: proposal.id })} />
      </Group>}
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
        {(state === 'not_booked' || (state === 'needs_time' && !proposal)) && (
          <Row label={state === 'needs_time' ? 'Check delivery times' : 'Try again'} chevron disabled={busy} onClick={() => bookAgain.mutate({ orderId: order.id })} />
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
              sub={order.can_confirm_absent ? "I've checked Instacart and confirmed there is no order" : 'Waiting for confirmation that booking stopped. Check Instacart in the meantime.'}
              chevron
              disabled={busy || !order.can_confirm_absent}
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
