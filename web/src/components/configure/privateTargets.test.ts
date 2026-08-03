import { describe, expect, it } from 'vitest'
import { groupPrivateTargets } from './privateTargets'

describe('groupPrivateTargets', () => {
  it('uses one batch group when discovery has no VPC metadata', () => {
    expect(
      groupPrivateTargets([
        { name: 'private-a', publicly_accessible: false },
        { name: 'public', publicly_accessible: true },
        { name: 'private-b', publicly_accessible: false },
      ])
    ).toEqual([
      {
        key: 'private-batch',
        label: 'Imported batch',
        targetNames: ['private-a', 'private-b'],
      },
    ])
  })

  it('groups private targets by VPC when discovery exposes VPC IDs', () => {
    expect(
      groupPrivateTargets([
        {
          name: 'orders',
          publicly_accessible: false,
          vpc_id: 'vpc-orders',
        },
        {
          name: 'users',
          publicly_accessible: false,
          vpc_id: 'vpc-users',
        },
        {
          name: 'orders-reader',
          publicly_accessible: false,
          vpc_id: 'vpc-orders',
        },
      ])
    ).toEqual([
      {
        key: 'vpc:vpc-orders',
        label: 'VPC vpc-orders',
        targetNames: ['orders', 'orders-reader'],
      },
      {
        key: 'vpc:vpc-users',
        label: 'VPC vpc-users',
        targetNames: ['users'],
      },
    ])
  })
})
