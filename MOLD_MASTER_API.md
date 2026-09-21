# Mold Master API

Run `database/mold_master_schema.sql` in MySQL before starting the API.

Base URL: `/api/mold-master`

## Location node types

- `GET /location-node-types`
- `GET /location-node-types/{type_id}`
- `POST /location-node-types`
- `PUT /location-node-types/{type_id}`
- `DELETE /location-node-types/{type_id}`

## Mold location tree

- `GET /location-nodes?tree=true`
- `GET /location-nodes/{node_id}`
- `POST /location-nodes`
- `PUT /location-nodes/{node_id}`
- `POST /location-nodes/{node_id}/move`
- `PATCH /location-nodes/{node_id}/status`
- `DELETE /location-nodes/{node_id}`

## Dynamic mold attributes

- `GET /attributes`
- `GET /attributes/{attribute_id}`
- `POST /attributes`
- `PUT /attributes/{attribute_id}`
- `DELETE /attributes/{attribute_id}`
- `GET /molds/{mold_id}/attribute-values`
- `PUT /molds/{mold_id}/attribute-values`

## Molds

- `GET /statistics`
- `GET /molds`
- `GET /molds/{mold_id}`
- `POST /molds`
- `PUT /molds/{mold_id}`
- `PATCH /molds/{mold_id}/status`
- `DELETE /molds/{mold_id}`

`POST /molds` and `PUT /molds/{mold_id}` accept fixed mold fields plus
`attribute_values` and `products`. The operation is atomic: if validation of
any dynamic attribute or product fails, the complete transaction is rolled back.

## Mold products

- `GET /products/lookup`
- `GET /molds/{mold_id}/products`
- `PUT /molds/{mold_id}/products`

The two `PUT` relation endpoints use full replacement semantics. Send the
complete current list; send an empty list to remove all relations or values.

Interactive request and response documentation is available at `/docs` after
the FastAPI service starts.
