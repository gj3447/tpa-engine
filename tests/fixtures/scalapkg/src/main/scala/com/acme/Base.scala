package com.acme

trait Base:
  def run(): Unit

def traced[A](body: A): A = body
