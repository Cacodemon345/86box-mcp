# BEEP.COM: 1 kHz on the PC speaker (PIT channel 2, divisor 1193) for 4 BIOS ticks (about 220 ms).
#   i586-pc-msdosdjgpp-as -o beep.o beep.s
#   i586-pc-msdosdjgpp-ld -Ttext=0x100 -e start --oformat binary -o BEEP.COM beep.o
.code16
        .globl start
start:
  movb $0xb6, %al
  outb %al, $0x43
  movw $1193, %ax
  outb %al, $0x42
  movb %ah, %al
  outb %al, $0x42
  inb $0x61, %al
  orb $3, %al
  outb %al, $0x61
  xorw %ax, %ax
  movw %ax, %es
  movw %es:0x46c, %bx
  addw $4, %bx
1: cmpw %es:0x46c, %bx
  jne 1b
  inb $0x61, %al
  andb $0xfc, %al
  outb %al, $0x61
  ret
