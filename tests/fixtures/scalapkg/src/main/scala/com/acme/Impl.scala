package com.acme.impl

import com.acme.Base
import com.acme.traced

@service
class Worker extends Base:
  @traced
  def run(): Unit =
    helper()
    Base

def helper(): Unit = ()
