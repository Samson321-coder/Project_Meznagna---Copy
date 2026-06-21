from typing import List, Optional
from datetime import datetime, timezone
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey, Text, BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    username: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    bank_name: Mapped[Optional[str]] = mapped_column(String(255))
    bank_account_number: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Last interaction; session ends after SESSION_TIMEOUT_MINUTES of inactivity (see bot.py).
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    transactions: Mapped[List["Transaction"]] = relationship("Transaction", back_populates="user")
    tickets: Mapped[List["Ticket"]] = relationship("Ticket", back_populates="user")

class Lottery(Base):
    __tablename__ = 'lotteries'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    image_file_id: Mapped[Optional[str]] = mapped_column(String(255))
    ticket_price: Mapped[float] = mapped_column(Float, default=500.0)
    total_tickets: Mapped[int] = mapped_column()
    sold_tickets: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    tickets: Mapped[List["Ticket"]] = relationship("Ticket", back_populates="lottery")

class Ticket(Base):
    __tablename__ = 'tickets'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'))
    lottery_id: Mapped[int] = mapped_column(ForeignKey('lotteries.id'))
    ticket_number: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(50), default='pending')  # 'pending', 'confirmed'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="tickets")
    lottery: Mapped["Lottery"] = relationship("Lottery", back_populates="tickets")
    transaction: Mapped[Optional["Transaction"]] = relationship("Transaction", back_populates="ticket")

class Transaction(Base):
    __tablename__ = 'transactions'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('users.id'))
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey('tickets.id'))  # Link to a specific ticket
    amount: Mapped[float] = mapped_column(Float)
    type: Mapped[Optional[str]] = mapped_column(String(50))  # 'deposit', 'purchase'
    status: Mapped[str] = mapped_column(String(50), default='pending')
    screenshot_file_id: Mapped[Optional[str]] = mapped_column(String(255))
    reference_number: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped["User"] = relationship("User", back_populates="transactions")
    ticket: Mapped[Optional["Ticket"]] = relationship("Ticket", back_populates="transaction")
