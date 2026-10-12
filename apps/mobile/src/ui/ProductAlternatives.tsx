import type { Product } from '../lib/api/types'
import { productCost, productSize } from '../lib/products'
import { Group, Row } from './Group'
import { BoughtBefore, ProductPicture } from './ShoppingList'
import styles from './ProductAlternatives.module.css'

interface ProductAlternativesProps {
  title: string
  products: readonly Product[]
  emptyReason?: string | null
  query?: string | null
  browse?: { label: string; onClick: () => void }
  onUse: (productId: string) => void
}

/** Product choices, shared by the curated alternatives and the broader search matches. */
export function ProductAlternatives({ title, products, emptyReason, query, browse, onUse }: ProductAlternativesProps) {
  if (products.length === 0 && !emptyReason && !browse) return null
  return (
    <Group
      title={`${title} (${products.length})`}
      footer={query ? `Results for “${query}”.` : undefined}
    >
      <ul className={styles.alternatives}>
        {products.length === 0 && emptyReason && <li><Row label="None" sub={emptyReason} /></li>}
        {products.map((product) => (
          <li key={product.product_id} className={styles.choice}>
            <ProductPicture product={product} size="small" />
            <span className={styles.altName}>
              <b>
                {product.bought_before && <BoughtBefore />}
                {product.name}
              </b>
              <small>
                {[productSize(product), query || product.count > 1 ? `Qty ${product.count}` : '']
                  .filter(Boolean)
                  .join(' · ')}
              </small>
            </span>
            <span className={styles.altPrice}>
              <b>{productCost(product) ?? 'No price'}</b>
              <button
                type="button"
                className={`${styles.use} cc-hit`}
                aria-label={`Use ${product.name}`}
                onClick={() => onUse(product.product_id)}
              >
                Use
              </button>
            </span>
          </li>
        ))}
        {browse && <li><Row label={browse.label} onClick={browse.onClick} chevron /></li>}
      </ul>
    </Group>
  )
}
