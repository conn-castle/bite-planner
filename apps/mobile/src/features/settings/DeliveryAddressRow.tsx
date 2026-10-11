import { type FormEvent, type ReactElement, useEffect, useState } from 'react'
import { apiRequest } from '../../lib/api/client'
import type { AddressSearchIn, AddressSuggestions, DeliveryAddress, Household } from '../../lib/api/types'
import { errorMessage } from '../../lib/errorMessage'
import { Button } from '../../ui/Button'
import { Group, Row } from '../../ui/Group'
import { InfoDialog } from '../../ui/InfoDialog'
import { Notice } from '../../ui/Notice'
import { SearchField } from '../../ui/SearchField'
import { TextField } from '../../ui/TextField'
import { useChangeHousehold } from './settings'
import styles from './DeliveryAddressRow.module.css'

function addressLabel(address: DeliveryAddress): string {
  const street: string = [address.street_address, address.apartment_number].filter(Boolean).join(', ')
  return `${street}, ${address.city}, ${address.state} ${address.postal_code}`
}

export function DeliveryAddressRow({ household }: { household: Household }): ReactElement {
  const [open, setOpen] = useState<boolean>(false)
  const [search, setSearch] = useState<string>('')
  const [selected, setSelected] = useState<DeliveryAddress | null>(null)
  const [unit, setUnit] = useState<string>('')
  // AWS autocomplete is for single use: these results never enter the persisted query cache.
  const [suggestions, setSuggestions] = useState<DeliveryAddress[] | null>(null)
  const [searching, setSearching] = useState<boolean>(false)
  const [resolving, setResolving] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const change = useChangeHousehold()
  const busy: boolean = resolving || change.isPending
  const destination: DeliveryAddress | null = selected && { ...selected, apartment_number: unit }

  useEffect(() => {
    if (!open || selected !== null || resolving || search.trim().length < 3) return
    let current: boolean = true
    const controller: AbortController = new AbortController()
    const timer: ReturnType<typeof setTimeout> = setTimeout(() => {
      setSearching(true)
      void apiRequest<AddressSuggestions>('POST', '/v1/household/delivery-address/suggestions', {
        query: search.trim(),
      } satisfies AddressSearchIn, undefined, controller.signal).then((reply: AddressSuggestions) => {
        if (current) setSuggestions(reply.addresses)
      }).catch((problem: unknown) => {
        if (current) setError(errorMessage(problem))
      }).finally(() => {
        if (current) setSearching(false)
      })
    }, 250)
    return () => {
      current = false
      controller.abort()
      clearTimeout(timer)
    }
  }, [open, search, selected, resolving])

  function close(): void {
    if (!busy) setOpen(false)
  }

  function find(value: string): void {
    setSearch(value)
    setSelected(null)
    setSuggestions(null)
    setSearching(false)
    setError(null)
    change.reset()
  }

  async function choose(address: DeliveryAddress): Promise<void> {
    setResolving(true)
    setSuggestions(null)
    setSearching(false)
    setError(null)
    try {
      const normalized: DeliveryAddress = await apiRequest<DeliveryAddress>(
        'POST', '/v1/household/delivery-address/normalize', address,
      )
      setSelected(normalized)
      setSearch(addressLabel(normalized))
    } catch (problem: unknown) {
      setError(errorMessage(problem))
    } finally {
      setResolving(false)
    }
  }

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (busy || destination === null) return
    try {
      await change.mutateAsync({ delivery_address: destination })
      setOpen(false)
    } catch {
      // The chosen address stays visible alongside the API's actionable error.
    }
  }

  return (
    <>
      <Row label="Delivery address" sub={household.delivery_address ? addressLabel(household.delivery_address) : 'Choose your delivery address'}
        chevron onClick={() => {
          const address: DeliveryAddress | null = household.delivery_address
          setSelected(address)
          setUnit(address?.apartment_number ?? '')
          setSearch(address ? addressLabel({ ...address, apartment_number: '' }) : '')
          setSuggestions(null)
          setError(null)
          setSearching(false)
          change.reset()
          setOpen(true)
        }} />
      <InfoDialog isOpen={open} onClose={close} icon="truck" title="Delivery address"
        footer={<Button block variant="secondary" disabled={busy} onClick={close}>Cancel</Button>}>
        <p>Find your address, then check it before saving. It must already be saved in Instacart.</p>
        <form className={styles.form} onSubmit={(event: FormEvent<HTMLFormElement>) => void save(event)} aria-busy={busy}>
          <fieldset className={styles.search} disabled={busy}>
            <SearchField value={search} onChange={find} placeholder="Start typing your address" />
          </fieldset>
          {searching && <p role="status">Finding addresses…</p>}
          {resolving && <p role="status">Checking address…</p>}
          {!busy && suggestions !== null && (
            <div aria-label="Address suggestions" role="region">
              {suggestions.length > 0 ? (
                <Group>
                  {suggestions.map((address: DeliveryAddress) => (
                    <Row key={addressLabel(address)} label={address.street_address}
                      sub={`${address.city}, ${address.state} ${address.postal_code}`}
                      onClick={() => void choose(address)} />
                  ))}
                </Group>
              ) : <p role="status">No addresses found. Add more of the street, city or ZIP code.</p>}
            </div>
          )}
          <TextField label="Apartment or unit (optional)" name="apartment" autoComplete="address-line2" maxLength={30}
            value={unit} onChange={(event) => setUnit(event.target.value)} disabled={busy} />
          {destination && <Group><Row label="Deliver to" sub={addressLabel(destination)} /></Group>}
          {(error || change.error) && <Notice>{error ?? change.error?.message}</Notice>}
          <Button type="submit" block disabled={busy || destination === null}>{change.isPending ? 'Saving…' : 'Save address'}</Button>
        </form>
      </InfoDialog>
    </>
  )
}
