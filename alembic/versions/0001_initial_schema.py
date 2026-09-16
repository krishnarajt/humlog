"""initial schema

hum.log's full schema: writers, their posts, the blocks a post is made of, the
images those blocks point at, and the comments readers leave.

Two things worth knowing before editing this file.

The `CURRENT_TIMESTAMP` server defaults are deliberate: that spelling is valid
on both Postgres (deployed) and SQLite (local dev), which `now()` is not.

`blocks.order_key` gets no index and is never sorted in SQL. It holds base-62
fractional indices, whose meaning depends on ASCII ordering, and a Postgres
database created with an `en_US.UTF-8` collation does not sort that way. The
application sorts a post's blocks in Python instead — see
app/blocks/ordering.py. If a SQL-side sort is ever wanted, the column needs
`COLLATE "C"`, not just an index.

Revision ID: 0001
Revises:
Create Date: 2026-08-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_uuid', sa.String(length=36), nullable=True),
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_user_uuid'), 'users', ['user_uuid'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)

    op.create_table('blogs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('blog_uuid', sa.String(length=36), nullable=True),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('slug', sa.String(length=120), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('cover_image_uuid', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('comments_enabled', sa.Boolean(), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('view_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('author_id', 'slug', name='uq_blog_slug_per_author')
    )
    op.create_index(op.f('ix_blogs_author_id'), 'blogs', ['author_id'], unique=False)
    op.create_index(op.f('ix_blogs_blog_uuid'), 'blogs', ['blog_uuid'], unique=True)
    op.create_index(op.f('ix_blogs_id'), 'blogs', ['id'], unique=False)
    op.create_index(op.f('ix_blogs_status'), 'blogs', ['status'], unique=False)
    op.create_index('ix_blogs_status_published_at', 'blogs', ['status', 'published_at'], unique=False)

    op.create_table('password_reset_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_password_reset_codes_code_hash'), 'password_reset_codes', ['code_hash'], unique=False)
    op.create_index(op.f('ix_password_reset_codes_id'), 'password_reset_codes', ['id'], unique=False)
    op.create_index(op.f('ix_password_reset_codes_user_id'), 'password_reset_codes', ['user_id'], unique=False)

    op.create_table('refresh_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=500), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_refresh_tokens_id'), 'refresh_tokens', ['id'], unique=False)
    op.create_index(op.f('ix_refresh_tokens_token'), 'refresh_tokens', ['token'], unique=True)

    op.create_table('comments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('comment_uuid', sa.String(length=36), nullable=True),
    sa.Column('blog_id', sa.Integer(), nullable=False),
    sa.Column('parent_comment_id', sa.Integer(), nullable=True),
    sa.Column('author_name', sa.String(length=80), nullable=True),
    sa.Column('author_email', sa.String(length=255), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['blog_id'], ['blogs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_comment_id'], ['comments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_comments_blog_created', 'comments', ['blog_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_comments_blog_id'), 'comments', ['blog_id'], unique=False)
    op.create_index(op.f('ix_comments_comment_uuid'), 'comments', ['comment_uuid'], unique=True)
    op.create_index(op.f('ix_comments_id'), 'comments', ['id'], unique=False)
    op.create_index(op.f('ix_comments_ip_hash'), 'comments', ['ip_hash'], unique=False)
    op.create_index(op.f('ix_comments_parent_comment_id'), 'comments', ['parent_comment_id'], unique=False)
    op.create_index(op.f('ix_comments_status'), 'comments', ['status'], unique=False)

    op.create_table('images',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('image_uuid', sa.String(length=36), nullable=True),
    sa.Column('blog_id', sa.Integer(), nullable=False),
    sa.Column('uploaded_by_id', sa.Integer(), nullable=False),
    sa.Column('image_name', sa.Text(), nullable=True),
    sa.Column('mime_type', sa.String(length=100), nullable=True),
    sa.Column('file_size', sa.Integer(), nullable=True),
    sa.Column('file_format', sa.String(length=10), nullable=True),
    sa.Column('object_key', sa.String(length=500), nullable=False),
    sa.Column('bucket', sa.String(length=100), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.ForeignKeyConstraint(['blog_id'], ['blogs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_images_blog_id'), 'images', ['blog_id'], unique=False)
    op.create_index(op.f('ix_images_id'), 'images', ['id'], unique=False)
    op.create_index(op.f('ix_images_image_uuid'), 'images', ['image_uuid'], unique=True)
    op.create_index(op.f('ix_images_uploaded_by_id'), 'images', ['uploaded_by_id'], unique=False)

    op.create_table('blocks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('block_uuid', sa.String(length=36), nullable=True),
    sa.Column('blog_id', sa.Integer(), nullable=False),
    sa.Column('parent_block_id', sa.Integer(), nullable=True),
    sa.Column('order_key', sa.String(length=128), nullable=False),
    sa.Column('block_type', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.Column('image_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['blog_id'], ['blogs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['image_id'], ['images.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['parent_block_id'], ['blocks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_blocks_block_uuid'), 'blocks', ['block_uuid'], unique=True)
    op.create_index(op.f('ix_blocks_blog_id'), 'blocks', ['blog_id'], unique=False)
    op.create_index('ix_blocks_blog_parent', 'blocks', ['blog_id', 'parent_block_id'], unique=False)
    op.create_index(op.f('ix_blocks_id'), 'blocks', ['id'], unique=False)
    op.create_index(op.f('ix_blocks_image_id'), 'blocks', ['image_id'], unique=False)
    op.create_index(op.f('ix_blocks_parent_block_id'), 'blocks', ['parent_block_id'], unique=False)

def downgrade() -> None:
    op.drop_index(op.f('ix_blocks_parent_block_id'), table_name='blocks')
    op.drop_index(op.f('ix_blocks_image_id'), table_name='blocks')
    op.drop_index(op.f('ix_blocks_id'), table_name='blocks')
    op.drop_index('ix_blocks_blog_parent', table_name='blocks')
    op.drop_index(op.f('ix_blocks_blog_id'), table_name='blocks')
    op.drop_index(op.f('ix_blocks_block_uuid'), table_name='blocks')

    op.drop_table('blocks')
    op.drop_index(op.f('ix_images_uploaded_by_id'), table_name='images')
    op.drop_index(op.f('ix_images_image_uuid'), table_name='images')
    op.drop_index(op.f('ix_images_id'), table_name='images')
    op.drop_index(op.f('ix_images_blog_id'), table_name='images')

    op.drop_table('images')
    op.drop_index(op.f('ix_comments_status'), table_name='comments')
    op.drop_index(op.f('ix_comments_parent_comment_id'), table_name='comments')
    op.drop_index(op.f('ix_comments_ip_hash'), table_name='comments')
    op.drop_index(op.f('ix_comments_id'), table_name='comments')
    op.drop_index(op.f('ix_comments_comment_uuid'), table_name='comments')
    op.drop_index(op.f('ix_comments_blog_id'), table_name='comments')
    op.drop_index('ix_comments_blog_created', table_name='comments')

    op.drop_table('comments')
    op.drop_index(op.f('ix_refresh_tokens_token'), table_name='refresh_tokens')
    op.drop_index(op.f('ix_refresh_tokens_id'), table_name='refresh_tokens')

    op.drop_table('refresh_tokens')
    op.drop_index(op.f('ix_password_reset_codes_user_id'), table_name='password_reset_codes')
    op.drop_index(op.f('ix_password_reset_codes_id'), table_name='password_reset_codes')
    op.drop_index(op.f('ix_password_reset_codes_code_hash'), table_name='password_reset_codes')

    op.drop_table('password_reset_codes')
    op.drop_index('ix_blogs_status_published_at', table_name='blogs')
    op.drop_index(op.f('ix_blogs_status'), table_name='blogs')
    op.drop_index(op.f('ix_blogs_id'), table_name='blogs')
    op.drop_index(op.f('ix_blogs_blog_uuid'), table_name='blogs')
    op.drop_index(op.f('ix_blogs_author_id'), table_name='blogs')

    op.drop_table('blogs')
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_index(op.f('ix_users_user_uuid'), table_name='users')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')

    op.drop_table('users')
