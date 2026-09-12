import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.product import PromotedProduct
from app.schemas.product import (
    ProductCreate,
    ProductListResponse,
    ProductRead,
    ProductUpdate,
)

router = APIRouter(
    prefix="/accounts/{account_id}/products",
    tags=["products"],
    dependencies=[Depends(require_admin)],
)


async def _require_account(account_id: uuid.UUID, db: AsyncSession) -> None:
    if await db.get(ZhihuAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="知乎账号不存在")


async def _require_product(
    account_id: uuid.UUID, product_id: uuid.UUID, db: AsyncSession
) -> PromotedProduct:
    result = await db.execute(
        select(PromotedProduct).where(
            PromotedProduct.id == product_id,
            PromotedProduct.account_id == account_id,
        )
    )
    product = result.scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="推广商品不存在")
    return product


@router.get("", response_model=ProductListResponse)
async def list_products(
    account_id: uuid.UUID,
    q: str = Query(default="", max_length=255),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> ProductListResponse:
    await _require_account(account_id, db)
    filters = [PromotedProduct.account_id == account_id]
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                PromotedProduct.name.ilike(pattern),
                PromotedProduct.category.ilike(pattern),
                PromotedProduct.description.ilike(pattern),
                PromotedProduct.selling_points.ilike(pattern),
            )
        )
    total = await db.scalar(select(func.count(PromotedProduct.id)).where(*filters))
    result = await db.execute(
        select(PromotedProduct)
        .where(*filters)
        .order_by(PromotedProduct.updated_at.desc(), PromotedProduct.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return ProductListResponse(items=list(result.scalars()), total=total or 0)


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    account_id: uuid.UUID,
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
) -> PromotedProduct:
    await _require_account(account_id, db)
    product = PromotedProduct(account_id=account_id, **payload.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PromotedProduct:
    return await _require_product(account_id, product_id, db)


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
) -> PromotedProduct:
    product = await _require_product(account_id, product_id, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    product = await _require_product(account_id, product_id, db)
    await db.delete(product)
    await db.commit()
